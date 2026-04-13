# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FastAPI backend for ECI OCR — handles user authentication, voter record management, and image upload/OCR job tracking. Uses PostgreSQL (Supabase) for the database, Supabase Storage for image files, JWT tokens for auth, Sarvam AI for OCR, and a custom HTML-aware parser for structured field extraction. Deployed on Render.com.

## Development Commands

```bash
# Activate virtual environment
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# API server (hot reload) — worker starts automatically as a daemon thread
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Run worker standalone (only needed if testing the worker in isolation)
python -m app.workers.ocr_worker

# Production server (matches Render config)
uvicorn app.main:app --host 0.0.0.0 --port 10000
```

No test framework or linting tools are configured.

## Architecture

Layered architecture with strict separation:

```
app/
├── main.py                          # FastAPI app init, table creation, starts worker thread on startup
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
│   ├── sarvam.py                    # Sarvam AI API client: job creation, file upload (ZIP), polling, result download
│   ├── smart_parser.py              # PRIMARY parser: HTML-aware, walks <td>/<th> cells, extracts all 9 fields
│   ├── parser.py                    # Regex parser (disabled in worker); plain-text input, needs db session
│   ├── claude_parser.py             # Claude API parser (disabled in worker); claude-sonnet-4-6
│   └── paddleocr_engine.py          # PaddleOCR engine (disabled in worker); Linux/Render only — crashes on macOS Apple Silicon
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

## Worker Architecture

The OCR worker is started as a **daemon thread** inside `main.py` via `@app.on_event("startup")`. It can also run as a standalone process (`python -m app.workers.ocr_worker`) for isolation or testing.

On Render, the worker runs embedded in the web service (current `render.yaml` has a single `eci-ocr-backend` web service). Deploying it as a separate Background Worker service is an option but not the current setup.

The worker has graceful shutdown via `threading.Event` + SIGINT/SIGTERM handlers. `_stop_event.wait(timeout=3)` is used instead of `time.sleep()` so it wakes immediately on shutdown signal.

**macOS — standalone worker:** the `__main__` block calls `multiprocessing.set_start_method("spawn", force=True)` which is required on macOS. Also, `main.py` sets these env vars via `os.environ.setdefault` before any paddle import — they won't be set when running standalone unless you export them first:
```bash
export FLAGS_call_stack_level=0
export GLOG_minloglevel=3
export OMP_NUM_THREADS=1
export PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
```

## OCR Engine Switching

The worker supports multiple OCR engines. Switch by toggling the import block at the top of `ocr_worker.py`:

| Engine | Output | Parser | Status |
|--------|--------|--------|--------|
| Sarvam | HTML | `parse_smart(text)` — no db | **ACTIVE** |
| PaddleOCR | plain text | `parse_ocr_text(text, db)` | Commented — local testing |
| claude_parser.py | structured | — | Commented fallback |

**PaddleOCR on macOS Apple Silicon** — the default PP-OCRv5 uses a server detection model (~500MB+) that OOM-kills the process. `core/paddleocr_engine.py` is already configured with explicit mobile models that work locally (PaddleOCR 3.x):
```python
PaddleOCR(
    text_detection_model_name="PP-OCRv4_mobile_det",
    text_recognition_model_name="devanagari_PP-OCRv5_mobile_rec",
    use_textline_orientation=False,
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
)
```
- `lang` + `ocr_version` shortcuts are ignored when model names are set explicitly
- PP-OCRv4 with `lang="hi"` does **not** work — v4 only supports `ch`/`en` via the shortcut; Hindi requires explicit model names as above
- **Do not use `signal.alarm()` (SIGALRM) inside the worker** — the worker runs as a daemon thread, and `signal` only works on the main thread; it will raise immediately
- Models download once to `~/.paddleocr/` on first run; clear with `rm -rf ~/.paddleocr/`
- `parse_smart` expects Sarvam's **HTML** output — it will return empty fields on PaddleOCR plain text; use `parse_ocr_text` from `core/parser.py` for plain text

## Parsing Strategy

**Active parser: `parse_smart`** from `core/smart_parser.py`:

1. Uses stdlib `HTMLParser` to extract `<td>` and `<th>` cell text (`<br/>` → `\n`).
2. **Pass 1** — header `<th>` cells: name, EPIC, address from left cell; serial, part, constituency, state from right cell.
3. **Pass 2** — adjacent label→value `<td>` pairs: mobile, district, state fallback.

Key OCR variant handling in `smart_parser.py`:
- Serial number: `_SERIAL_KEYWORDS` — matches `कण|क्रम|कम` (OCR corrupts क्रम → कम)
- Constituency: `_CONSTITUENCY_KEYWORDS` — matches `विधानसभा|निधानसभा` (OCR corrupts विधानसभा → निधानसभा)
- Pass 1 conditions use `any(kw in cell for kw in _KEYWORD_CONSTANT)` — do NOT use `or "string"` pattern (always truthy)
- EPIC: labeled values trusted without format gating; bare tokens use strict format validation
- Confidence: base `0.4` + up to `0.59` from `format_valid`, `label_match`, `clean`, `db_match`; capped at `0.99`

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
GET    /ocr/result/{job_id}     # Returns status; if completed: data field; if failed: error field
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

1. `POST /ocr/upload` — HEIC/HEIF converted to JPEG, uploaded to Supabase Storage at `{user_id}/{job_id}/{uuid}.{ext}`, Job row created with `status="pending"` and `is_cropped` flag.
2. Worker polls every 3s; picks oldest pending job → marks `processing`.
3. Image preprocessing: if `is_cropped=True` uses as-is; otherwise `crop_rois()` extracts `[0:25%h, 0:60%w]` (structured data) + `[25%h:55%h, :]` (form/mobile section) and concatenates vertically.
4. Sarvam AI (`core/sarvam.py`): uploads as ZIP, starts OCR job, polls (up to 30 retries × 4s), downloads HTML result.
5. `parse_smart(ocr_text)` extracts all 9 fields. If `name` found → saved; else `parsed={}`.
6. Job updated to `completed` with `{raw_text, parsed}` or `failed` with `error_message`.

**Note:** `image_processing.py` also contains `enhance_cropped`, `enhance_printed`, and `enhance_handwritten` functions, but the active worker pipeline does **not** call them — only `download_image` and `crop_rois` are used.

## Environment Variables

```
DATABASE_URL                    # PostgreSQL connection string (Supabase)
SECRET_KEY                      # JWT signing key
ALGORITHM                       # JWT algorithm (HS256)
ACCESS_TOKEN_EXPIRE_MINUTES     # Token TTL (default: 60)
SUPABASE_URL                    # Supabase project URL
SUPABASE_SERVICE_ROLE_KEY       # Supabase service role key
SARVAM_BASE_URL                 # Sarvam AI API endpoint
SARVAM_API_KEY                  # Sarvam AI authentication key
ANTHROPIC_API_KEY               # Claude API key (claude_parser.py — disabled in worker)
PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True  # Skips PaddleOCR connectivity check on startup
```

The following are set programmatically in `main.py` via `os.environ.setdefault` before any paddle import. Set them manually when running the worker standalone on macOS:
```
FLAGS_call_stack_level=0        # Suppresses PaddlePaddle C++ stack traces
GLOG_minloglevel=3              # Silences PaddlePaddle GLOG output
OMP_NUM_THREADS=1               # Prevents OpenMP thread oversubscription on Apple Silicon
```

## Render Deployment

Current `render.yaml` defines a **single web service** (`eci-ocr-backend`) that runs both the FastAPI app and the embedded OCR worker daemon thread. Start command: `uvicorn app.main:app --host 0.0.0.0 --port 10000`.

PaddleOCR requires **Standard plan (2GB RAM)** on Render if the PaddleOCR engine is active. Free/Starter (512MB) is insufficient.

## Runtime Directories

- `debug/` — debug images from `save_debug_images()`; runtime only, not in git
- `exports/` — CSV files from `csv_service.py`; runtime only, not in git

## API Collection

`eci_ocr_backend.postman_collection.json` — full Postman collection with auto-login and token refresh. Set `username`, `password`, `base_url` in collection variables.
