# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FastAPI backend for ECI OCR — handles user authentication, voter record management, and image upload/OCR job tracking. Uses PostgreSQL (Supabase) for the database, Supabase Storage for image files, JWT tokens for auth, and a local VLM for OCR. Deployed on Render.com.

## Development Commands

```bash
# Activate virtual environment
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# chandra-ocr[hf] is NOT in requirements.txt — must be installed separately
pip install chandra-ocr[hf]

# CUDA PyTorch (RTX 4060 / cu128) — replaces the CPU torch from requirements.txt
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

# API server (hot reload) — worker starts automatically as a daemon thread
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Run worker standalone (only needed if testing the worker in isolation)
python -m app.workers.ocr_worker

# Production server (matches Render config)
uvicorn app.main:app --host 0.0.0.0 --port 10000
```

No test framework or linting tools are configured. Python 3.11 (see `runtime.txt`).

## Architecture

Layered architecture with strict separation:

```
app/
├── main.py                          # FastAPI app init, table creation (create_all), starts worker thread on startup
├── api/
│   ├── deps.py                      # Depends: DB session, current user extraction
│   └── routes/
│       ├── auth.py                  # POST /auth/register, POST /auth/login, GET /auth/getUser
│       ├── ocr.py                   # POST /ocr/upload, GET /ocr/result/{job_id}; HEIC→JPEG conversion here
│       └── voter.py                 # GET/POST/PUT/DELETE voter endpoints + count + CSV export
├── schemas/
│   ├── auth.py                      # RegisterRequest (with role/hierarchy fields), LoginRequest, UserResponse
│   ├── ocr.py                       # UploadResponse
│   ├── voter.py                     # VoterCreate schema
│   └── voter_update_request.py      # VoterUpdate schema
├── services/
│   ├── auth_service.py              # Register/login logic; validates booth/constituency/district/mandal IDs
│   ├── vote_service.py              # Role-based voter query filtering
│   └── csv_service.py               # CSV file generation to exports/ directory
├── repositories/
│   ├── user_repo.py                 # User CRUD
│   ├── job_repo.py                  # Job CRUD: create_job, get_job_by_id, update_job_status
│   └── voter_repo.py                # Voter CRUD with composite PK (id + assembly_constituency_id)
├── models/
│   ├── user.py                      # UUID PK, bcrypt password, role, hierarchical IDs
│   ├── job.py                       # status, result JSON, error_message, is_cropped
│   ├── voter.py                     # Composite PK (id + assembly_constituency_id), full voter profile
│   ├── constituency.py              # Assembly constituencies (English + Hindi names)
│   ├── districts.py                 # Districts with mandal mapping
│   └── booths.py                    # Polling booths linked to constituencies
├── core/
│   ├── config.py                    # Settings loaded from .env via os.getenv
│   ├── security.py                  # JWT creation/decoding, bcrypt hashing
│   ├── storage.py                   # Supabase Storage client; upload_image() → ocr-images bucket
│   ├── image_processing.py          # OpenCV preprocessing: crop_rois, enhance_cropped/printed/handwritten
│   ├── smart_parser.py              # PRIMARY parser: HTML+plain-text aware, 3-pass extraction of 9 fields
│   ├── constituency_resolver.py     # Fuzzy-matches raw OCR constituency string → canonical Hindi name via DB (65% threshold)
│   └── chandra_ocr_engine.py        # ChandraOCR engine (ACTIVE); datalab-to/chandra-ocr-2 5B VLM; bf16 GPU / fp32 CPU; thread-timeout wrapper (120s GPU / 600s CPU); uses chandra-ocr[hf] library
├── workers/
│   └── ocr_worker.py                # Polling worker — started as daemon thread by main.py; can also run standalone
├── db/
│   ├── base.py                      # SQLAlchemy declarative Base
│   ├── base_model.py                # Aggregates all model imports (for Alembic/table awareness)
│   └── session.py                   # SessionLocal factory
└── utils/
    ├── exceptions.py                # AppException with standardized error format
    └── success_response.py          # Standard success response wrapper
```

**Request flow:** Route → Schema validation → Service (auth/voter) or direct repo call (OCR) → Repository → DB

**No Alembic migrations** — schema is created via `Base.metadata.create_all()` at startup in `main.py`. Schema changes apply immediately on next deploy.

## Worker Architecture

The OCR worker is started as a **daemon thread** inside `main.py` via `@app.on_event("startup")`. It can also run as a standalone process (`python -m app.workers.ocr_worker`) for isolation or testing.

On Render, the worker runs embedded in the web service (current `render.yaml` has a single `eci-ocr-backend` web service). Deploying it as a separate Background Worker service is an option but not the current setup.

The worker has graceful shutdown via `threading.Event` + SIGINT/SIGTERM handlers. `_stop_event.wait(timeout=3)` is used instead of `time.sleep()` so it wakes immediately on shutdown signal. Signal registration is wrapped in `try/except (OSError, ValueError): pass` — it silently fails when the worker runs as a daemon thread (signals only work on the main thread), but the `_stop_event` still triggers on Render's SIGTERM via the main thread.

## OCR Engine

**ChandraOCR** (`core/chandra_ocr_engine.py`) — sole active engine. Model: `datalab-to/chandra-ocr-2` (5B params, BF16). 90+ languages including Hindi/Devanagari. No HF_TOKEN required (public model, Apache 2.0). Uses `chandra-ocr[hf]` library: `AutoModelForImageTextToText` + `generate_hf` + `parse_markdown`. `prompt_type="ocr_layout"` for structured extraction. bf16 on GPU, fp32 on CPU. Thread-timeout: 120s GPU / 600s CPU. Install: `pip install chandra-ocr[hf]`.

## Parsing Strategy

**Active parser: `parse_smart`** from `core/smart_parser.py` — called in the worker for every job. Handles two structural patterns:

- **Pattern A** (HTML table): Primary data in `<td>/<th>` cells — header cell has name/EPIC/address, adjacent cell has serial/part/constituency/state.
- **Pattern B** (plain text): Primary data as `"label: value\n"` lines **before** any `<table>` tag; supplemental data still in later tables.

Three passes in order:

1. **Pass 1 — plain-text pre-table section**: Runs first. Extracts Pattern B fields (name/EPIC/address/serial/…) from text before the first `<table>`. Critical: these results are never overwritten by later passes, preventing data bleeding from repeated label sections (e.g. BLO's name in a "पिछले SIR" sub-table).
2. **Pass 2 — header cells** (multi-field, `\n`-delimited): Walks `<th>`/`<td>` cells for Pattern A. Sets a field only if Pass 1 left it empty.
3. **Pass 3 — adjacent label→value cell pairs**: Extracts mobile, district, state from consecutive cell pairs.

Key OCR variant handling in `smart_parser.py`:
- Serial number: `_SERIAL_PREFIX` regex — matches `क्रम|कम|कंप|कण|डम|ब्लॉक|ग्राम` (क्रम संख्या corruptions)
- Constituency: triggers on `"विधानसभा"`, `"निधानसभा"`, or `"क्षेत्र का"` in cell
- Pass 2 conditions use `any(kw in cell for kw in _NAME_KEYWORDS)` — do NOT use `or "string"` pattern (always truthy)
- EPIC: 6 recognized patterns including pure-digit EPICs; labeled values trusted without format gating; bare tokens use strict format validation
- Confidence: base `0.4` + up to `0.59` from `format_valid`, `label_match`, `clean`, `db_match`; capped at `0.99`. Exceptions: `serial_number` hardcoded to `0.97` if found (bypasses base formula); `state` hardcoded to `0.99` if value == `"उत्तर प्रदेश"`

**Output shape** — all parsers produce the same structure:
```json
{"name": {"value": "...", "confidence": 0.95}, "epic": {"value": "...", "confidence": 0.95}, ...}
```

## Key Patterns

- **Dependency Injection**: DB session and current user via `Depends()` in `api/deps.py`
- **Repository Pattern**: All DB access goes through repo files; routes and services never query directly
- **Standardized errors**: Raise `AppException(status_code, code, message)` — `code` is a string key (e.g. `"JOB_NOT_FOUND"`)
- **Standardized success**: Use `success_response.py` wrapper for consistent JSON shape
- **JWT auth**: `OAuth2PasswordBearer` scheme; token extracted and decoded in `deps.py`
- **Role-based filtering**: `vote_service.py` filters voter queries by role (`superadmin` sees all; `booth`/`constituency`/`district`/`mandal` see only their scope)

## Role Hierarchy

| Role | Scope | Required Fields |
|------|-------|-----------------|
| `superadmin` | All voters | — |
| `district` | District voters | `district_id` |
| `mandal` | Mandal voters | `mandal_id` |
| `constituency` | Constituency voters | `constituency_id` |
| `booth` | Own uploaded voters only | `booth_id` |

`auth_service.register_user()` validates these IDs against the DB and auto-populates hierarchical fields.

## API Endpoints

### Auth
```
POST   /auth/register           # Create user (role + hierarchy IDs required)
POST   /auth/login              # Returns JWT token
GET    /auth/getUser            # Current authenticated user
```

### OCR
```
POST   /ocr/upload              # Multipart: file (jpeg/png/jpg/webp/svg/heic/heif, ≤10MB) + optional isCropped bool
GET    /ocr/result/{job_id}     # Polling endpoint; response shape varies by status:
                                #   pending/processing → {"job_id": "...", "status": "pending"|"processing"}
                                #   failed             → {"job_id": "...", "status": "failed", "error": "..."}
                                #   completed          → {"job_id": "...", "status": "completed", "data": {"raw_text": "...", "parsed": {...}}}
```

### Voter
```
GET    /voter/getVoters         # ?page=&limit= pagination; ?epic= EPIC search scoped to booth_id (both paths return paginated shape); 204 when no results
POST   /voter/save              # Create voter — caller sends assembly_constituency_name (Hindi or English); server auto-populates assembly_constituency_id, district_id, mandal_id, booth_id, user_id
PUT    /voter/{voter_id}        # Update voter; requires ?ac_id= query param; does NOT re-validate or re-populate hierarchy fields
DELETE /voter/{voter_id}        # Delete voter; requires ?ac_id= query param
GET    /voter/count             # Total voter count (global — not role-filtered)
GET    /voter/export            # Export CSV; filters: name, mobile, epic, assembly_constituency_id, district_id
```

**There is no `GET /voter/{voter_id}` single-voter lookup endpoint.** The voter composite PK is `(id, assembly_constituency_id)`; both are required for update/delete via `voter_id` + `?ac_id=`.

## OCR Job Flow

1. `POST /ocr/upload` — HEIC/HEIF converted to JPEG (quality=95), uploaded to Supabase Storage at `{user_id}/{job_id}/{uuid}.{ext}`, Job row created with `status="pending"` and `is_cropped` flag.
2. Worker polls every 3s; picks oldest pending job → marks `processing`.
3. Image preprocessing: if `is_cropped=True` uses as-is; otherwise `crop_rois()` extracts `[0:25%h, 0:60%w]` (structured data) + `[25%h:55%h, :]` (form/mobile section) and concatenates vertically.
4. **ChandraOCR** runs `datalab-to/chandra-ocr-2` on the preprocessed image using `prompt_type="ocr_layout"`, returns Markdown/HTML via `parse_markdown()`.
5. **`parse_smart`** runs on the OCR output (3-pass extraction as described above).
6. **`resolve_constituency`** runs in-band: fuzzy-matches the raw OCR constituency string against DB (65% threshold → canonical Hindi name + district). If ambiguous or unmatched, sets constituency value to `None` and confidence to `0.0`.
7. Job updated to `completed` with `{"raw_text": ..., "parsed": {...}}` or `failed` with `error_message`.

**Note:** `image_processing.py` also contains `enhance_cropped`, `enhance_printed`, `enhance_handwritten`, `normalize_lighting`, and `remove_shadow` functions, but the active worker pipeline does **not** call them — only `download_image` and `crop_rois` are used.

## Environment Variables

```
DATABASE_URL                    # PostgreSQL connection string (Supabase)
SECRET_KEY                      # JWT signing key
ALGORITHM                       # JWT algorithm (HS256)
ACCESS_TOKEN_EXPIRE_MINUTES     # Token TTL (default: 60)
SUPABASE_URL                    # Supabase project URL
SUPABASE_SERVICE_ROLE_KEY       # Supabase service role key
```

`HF_TOKEN`, `SARVAM_BASE_URL`, `SARVAM_API_KEY`, and `ANTHROPIC_API_KEY` appear in `.env` but are not used by any active code path.

## Render Deployment

Current `render.yaml` defines a **single web service** (`eci-ocr-backend`) that runs both the FastAPI app and the embedded OCR worker daemon thread. Start command: `uvicorn app.main:app --host 0.0.0.0 --port 10000`.

The active ChandraOCR engine (`datalab-to/chandra-ocr-2`, 5B params) benefits from a GPU but can run on CPU. Install `chandra-ocr[hf]` alongside the standard requirements.

## AWS Deployment

**Recommended instance: `g6.xlarge`** (confirmed available in `ap-south-1` / Mumbai) — L4 GPU (24 GB VRAM), ~$0.81/hr on-demand.

**Setup checklist:**
- **AMI**: "Deep Learning Base OSS Nvidia Driver GPU AMI" (AWS Marketplace) — ships with CUDA, cuDNN, PyTorch
- **Storage**: ≥50 GB EBS gp3 — model weights are ~15 GB download + OS + venv
- **Security group**: open inbound port 8000 (dev) / 10000 (prod)
- **First boot**: `chandra_warmup()` is called at worker startup so the model pre-loads before the first job arrives

## Runtime Directories

- `debug/` — debug images from `save_debug_images()`; runtime only, not in git
- `exports/` — CSV files from `csv_service.py`; runtime only, not in git

## API Collection

`eci_ocr_backend.postman_collection.json` — full Postman collection with auto-login and token refresh. Set `username`, `password`, `base_url` in collection variables.
