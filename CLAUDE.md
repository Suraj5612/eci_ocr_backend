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
│   ├── smolvlm_engine.py            # SmolVLM engine (disabled — local testing only); HuggingFaceTB/SmolVLM2-2.2B-Instruct; ~4.5 GB download; requires num2words; AutoModelForImageTextToText (transformers 5.x) with 4.x fallback
│   ├── chandra_ocr_engine.py        # ChandraOCR engine (ACTIVE); datalab-to/chandra-ocr-2 5B VLM; bf16 GPU / fp32 CPU; thread-timeout wrapper (120s GPU / 600s CPU); uses chandra-ocr[hf] library
│   ├── minicpm_v_engine.py          # MiniCPM-V engine (disabled); openbmb/MiniCPM-V-2_6 8B VLM; FORCE_4BIT flag; thread-timeout wrapper (120s GPU / 900s CPU); 4 transformers 5.x compatibility patches
│   ├── paddleocr_engine.py          # Classic PaddleOCR engine (disabled); Linux/Render only — crashes on macOS Apple Silicon
│   └── paddleocr_vl_engine.py       # PaddleOCR-VL engine (disabled); 0.9B Qwen2.5-VL VLM; has 3 programmatic transformers 5.x patches
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

The worker has graceful shutdown via `threading.Event` + SIGINT/SIGTERM handlers. `_stop_event.wait(timeout=3)` is used instead of `time.sleep()` so it wakes immediately on shutdown signal. Signal registration is wrapped in `try/except (OSError, ValueError): pass` — it silently fails when the worker runs as a daemon thread (signals only work on the main thread), but the `_stop_event` still triggers on Render's SIGTERM via the main thread.

## OCR Engine Switching

The worker supports multiple OCR engines. Switch by toggling the import block at the top of `ocr_worker.py` and updating the `process_job` call:

| Engine | Output | Parser | Status |
|--------|--------|--------|--------|
| ChandraOCR | Markdown/HTML | `parse_smart(text)` — raw_text only | **ACTIVE** |
| MiniCPM-V | HTML/Markdown | `parse_smart(text)` — raw_text only | Commented — needs GPU |
| SmolVLM-2.2B | HTML/Markdown | `parse_smart(text)` — raw_text only | Commented — local testing |
| PaddleOCR-VL | HTML/Markdown | `parse_smart(text)` — no db | Commented — previous path |
| Sarvam | HTML | `parse_smart(text)` — no db | Commented — previous production path |
| PaddleOCR (classic) | plain text | `parse_ocr_text(text, db)` | Commented — local testing |

**ChandraOCR** (`core/chandra_ocr_engine.py`) — **ACTIVE**. Model: `datalab-to/chandra-ocr-2` (5B params, BF16). State-of-the-art OCR, 90+ languages including Hindi/Devanagari. No HF_TOKEN required (public model, Apache 2.0 code license). Uses `chandra-ocr[hf]` library: `AutoModelForImageTextToText` + `generate_hf` + `parse_markdown`. `prompt_type="ocr_layout"` for structured extraction. bf16 on GPU, fp32 on CPU. Thread-timeout: 120s GPU / 600s CPU. Install: `pip install chandra-ocr[hf]`.

**MiniCPM-V** (`core/minicpm_v_engine.py`) — commented out. Model: `openbmb/MiniCPM-V-2_6` (8B params, Qwen2 LLM + SigLIP vision). `FORCE_4BIT = True` flag forces 4-bit NF4 regardless of VRAM (set `False` for L4/A100 deployment). VRAM-aware strategy: 4-bit NF4 (bitsandbytes, `bnb_4bit_use_double_quant=True`, ~4.5 GB) for <22 GB VRAM; bf16/fp16 for ≥22 GB VRAM; fp32 on CPU. Thread-timeout: 120s GPU / 900s CPU. HF_TOKEN **required** — raises `EnvironmentError` at load time if missing. Four transformers 5.x compatibility patches applied at load time in `_load()`:
1. `PreTrainedModel.all_tied_weights_keys` — set to `{}` if missing/wrong type
2. `dtype` kwarg — uses `dtype=` not `torch_dtype=` for model loading
3. Tokenizer attribute patch — adds `im_start_id`, `im_end_id`, `slice_start_id`, `slice_end_id` via `convert_tokens_to_ids`
4. Processor pre-load — patches internal tokenizer before first `model.chat()` call

**SmolVLM2-2.2B** (`core/smolvlm_engine.py`) — commented out (local testing). Model: `HuggingFaceTB/SmolVLM2-2.2B-Instruct` (SigLIP vision + SmolLM2 LM, 2.2B params). ~4.5 GB download, no HF_TOKEN required. fp16 on CUDA (~5 GB VRAM), fp32 on CPU. Uses `AutoModelForImageTextToText` (transformers 5.x) with `AutoModelForVision2Seq` fallback for 4.x. Thread-timeout: 120s GPU / 600s CPU. Decodes only newly generated tokens (strips prompt echo via `generated_ids[:, input_ids.shape[1]:]`). Weaker on Hindi/Devanagari than MiniCPM-V. Requires `num2words` (`pip install num2words`).

**PaddleOCR-VL** (`core/paddleocr_vl_engine.py`) — commented out. Has 3 programmatic compatibility patches for transformers 5.x (applied at import time, not in cache files): (1) restores `'default'`/`'mrope'` in `ROPE_INIT_FUNCTIONS`, (2) monkey-patches `PreTrainedModel._init_weights` to inject `compute_default_rope_parameters`, (3) wraps `prepare_inputs_for_generation` to handle `cache_position=None`. Uses `from_config` + manual safetensors loading to bypass accelerate meta-tensor init.

**Classic PaddleOCR on macOS Apple Silicon** — `core/paddleocr_engine.py` is configured with explicit mobile models to avoid OOM (default PP-OCRv5 server model is too large):
```python
PaddleOCR(
    text_detection_model_name="PP-OCRv4_mobile_det",
    text_recognition_model_name="devanagari_PP-OCRv5_mobile_rec",
    use_textline_orientation=False,
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
)
```
- PP-OCRv4 with `lang="hi"` does **not** work — v4 only supports `ch`/`en` via the shortcut; Hindi requires explicit model names as above
- **Do not use `signal.alarm()` (SIGALRM) inside the worker** — the worker runs as a daemon thread; `signal` only works on the main thread
- `parse_smart` expects Sarvam's **HTML** output — use `parse_ocr_text` from `core/parser.py` for PaddleOCR plain text

## Parsing Strategy

**Active parser: `parse_smart`** from `core/smart_parser.py` (currently disabled in worker — parsing re-enable pending output format verification):

1. Uses stdlib `HTMLParser` to extract `<td>` and `<th>` cell text (`<br/>` → `\n`).
2. **Pass 1** — header `<th>` cells: name, EPIC, address from left cell; serial, part, constituency, state from right cell.
3. **Pass 2** — adjacent label→value `<td>` pairs: mobile, district, state fallback.

Key OCR variant handling in `smart_parser.py`:
- Serial number: `_SERIAL_KEYWORDS` — matches `कण|क्रम|कम` (OCR corrupts क्रम → कम)
- Constituency: `_CONSTITUENCY_KEYWORDS` — matches `विधानसभा|निधानसभा` (OCR corrupts विधानसभा → निधानसभा)
- Pass 1 conditions use `any(kw in cell for kw in _KEYWORD_CONSTANT)` — do NOT use `or "string"` pattern (always truthy)
- EPIC: labeled values trusted without format gating; bare tokens use strict format validation
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
                                #   completed          → {"job_id": "...", "status": "completed", "data": {"raw_text": "..."}}
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
4. **Current active engine — ChandraOCR** (`core/chandra_ocr_engine.py`): runs `datalab-to/chandra-ocr-2` (5B VLM) on the preprocessed image using `prompt_type="ocr_layout"`, returns Markdown/HTML via `parse_markdown()` — compatible with `parse_smart()`.
5. Parsing is **currently disabled** — job is saved with `{raw_text}` only (no `parsed` field). Root cause: `parse_smart` was designed for Sarvam's HTML output (tables with `<td>/<th>` cells), but ChandraOCR returns plain Markdown. The `smart_parser` `HTMLParser` finds no table cells in Markdown and returns empty fields. Re-enabling requires either adapting `smart_parser` to handle Markdown or post-processing ChandraOCR output into HTML tables first.
6. Job updated to `completed` with `{raw_text}` or `failed` with `error_message`.

**Previous production path (Sarvam + parse_smart, currently commented out):**
- Sarvam AI (`core/sarvam.py`): uploads as ZIP, starts OCR job, polls (up to 30 retries × 4s), downloads HTML result.
- `parse_smart(ocr_text)` extracts all 9 fields. If `name` found → saved as `parsed`; else `parsed={}`.
- Job saved with `{raw_text, parsed}`.

**Note:** `image_processing.py` also contains `enhance_cropped`, `enhance_printed`, `enhance_handwritten`, `normalize_lighting`, and `remove_shadow` functions, but the active worker pipeline does **not** call them — only `download_image` and `crop_rois` are used.

## Environment Variables

```
DATABASE_URL                    # PostgreSQL connection string (Supabase)
SECRET_KEY                      # JWT signing key
ALGORITHM                       # JWT algorithm (HS256)
ACCESS_TOKEN_EXPIRE_MINUTES     # Token TTL (default: 60)
SUPABASE_URL                    # Supabase project URL
SUPABASE_SERVICE_ROLE_KEY       # Supabase service role key
SARVAM_BASE_URL                 # Sarvam AI API endpoint (disabled — Sarvam engine commented out)
SARVAM_API_KEY                  # Sarvam AI authentication key (disabled)
ANTHROPIC_API_KEY               # Claude API key (claude_parser.py — disabled in worker)
HF_TOKEN                        # HuggingFace token — NOT required for active engine (chandra-ocr-2 is public). Still needed if switching back to MiniCPM-V-2_6 (gated model).
PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True  # Skips PaddleOCR connectivity check — always set by main.py via os.environ.setdefault regardless of active engine
```

The following are set programmatically in `main.py` via `os.environ.setdefault` before any paddle import. Only relevant if re-enabling the PaddleOCR engine:
```
FLAGS_call_stack_level=0        # Suppresses PaddlePaddle C++ stack traces
GLOG_minloglevel=3              # Silences PaddlePaddle GLOG output
OMP_NUM_THREADS=1               # Prevents OpenMP thread oversubscription on Apple Silicon
```

## Render Deployment

Current `render.yaml` defines a **single web service** (`eci-ocr-backend`) that runs both the FastAPI app and the embedded OCR worker daemon thread. Start command: `uvicorn app.main:app --host 0.0.0.0 --port 10000`.

The active ChandraOCR engine (`datalab-to/chandra-ocr-2`, 5B params) benefits from a GPU but can run on CPU. Install `chandra-ocr[hf]` alongside the standard requirements. No `bitsandbytes` required unless switching back to MiniCPM-V.

## AWS Deployment

**Recommended instance: `g6.xlarge`** (confirmed available in `ap-south-1` / Mumbai).

| Instance | GPU | VRAM | Strategy for MiniCPM-V | On-demand |
|---|---|---|---|---|
| `g6.xlarge` | L4 (Ada Lovelace) | 24 GB | fp16/bf16 (≥22 GB threshold) — no bitsandbytes needed | ~$0.81/hr |
| `g4dn.xlarge` | T4 (Turing) | 16 GB | 4-bit NF4 (<22 GB threshold) — bitsandbytes required | ~$0.53/hr |

**Setup checklist for g6.xlarge:**
- **AMI**: "Deep Learning Base OSS Nvidia Driver GPU AMI" (AWS Marketplace) — ships with CUDA, cuDNN, PyTorch
- **Storage**: ≥50 GB EBS gp3 — model weights alone are ~15 GB download + OS + venv
- **Security group**: open inbound port 8000 (dev) / 10000 (prod)
- **HF_TOKEN**: not required for active engine (chandra-ocr-2 is public)
- **First boot**: model downloads on first job — `chandra_warmup()` is already called inside `ocr_worker.py` at worker startup (which runs as a daemon thread from `main.py`), so the model pre-loads before the first job arrives. No changes needed.

**Engine VRAM usage on L4 (24 GB):**
- MiniCPM-V-2_6 bf16/fp16: ~16 GB — engine auto-selects bf16 (L4 supports it) since VRAM ≥ 22 GB (hardcoded threshold in `minicpm_v_engine.py` line 112)
- PaddleOCR-VL bf16: ~2.5 GB
- Both loaded simultaneously: ~18.5 GB — fits with ~5.5 GB headroom

**Do NOT import both `paddleocr_vl_engine` and `minicpm_v_engine` in the same worker process.** `paddleocr_vl_engine.py` monkey-patches `PreTrainedModel._init_weights` globally at import time (Patch 2) — this can interfere with MiniCPM-V's model loading. The worker uses mutually exclusive imports; keep it that way.

## Runtime Directories

- `debug/` — debug images from `save_debug_images()`; runtime only, not in git
- `exports/` — CSV files from `csv_service.py`; runtime only, not in git

## API Collection

`eci_ocr_backend.postman_collection.json` — full Postman collection with auto-login and token refresh. Set `username`, `password`, `base_url` in collection variables.
