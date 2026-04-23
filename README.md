# ECI OCR Backend

FastAPI backend for the ECI (Election Commission of India) voter card OCR system. Handles user authentication with role-based access control, voter record management, and image upload with AI-powered OCR processing using a 5B-parameter Vision Language Model.

---

## Table of Contents

- [Tech Stack](#tech-stack)
- [Architecture](#architecture)
- [OCR Pipeline](#ocr-pipeline)
- [Role Hierarchy](#role-hierarchy)
- [Local Development Setup](#local-development-setup)
- [Environment Variables](#environment-variables)
- [API Reference](#api-reference)
- [Deployment](#deployment)
- [Hardware Guide](#hardware-guide)
- [Performance Optimization](#performance-optimization)
- [Important Commands Reference](#important-commands-reference)
- [Known Gotchas](#known-gotchas)

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API Framework | FastAPI 0.115 + Uvicorn 0.30 |
| Database | PostgreSQL via Supabase |
| ORM | SQLAlchemy 2.0 |
| File Storage | Supabase Storage (S3-compatible) |
| Auth | JWT (python-jose) + bcrypt |
| OCR Model | `datalab-to/chandra-ocr-2` (5B params, BF16) |
| ML Runtime | PyTorch + HuggingFace Transformers |
| Image Processing | OpenCV + Pillow |
| Fuzzy Matching | rapidfuzz |
| HEIC Support | pillow-heif |
| Python Version | 3.11 |

---

## Architecture

```
app/
├── main.py                    # App init, DB table creation, starts worker thread
├── api/
│   ├── deps.py                # DB session + JWT auth via Depends()
│   └── routes/
│       ├── auth.py            # Register / Login / GetUser
│       ├── ocr.py             # Upload image / Poll result
│       └── voter.py           # Voter CRUD + count + CSV export
├── services/
│   ├── auth_service.py        # Registration logic, hierarchy ID validation
│   ├── vote_service.py        # Role-based voter query scoping
│   └── csv_service.py         # CSV export to exports/ directory
├── repositories/
│   ├── user_repo.py
│   ├── job_repo.py            # create_job, get_job_by_id, update_job_status
│   └── voter_repo.py          # Composite PK: (id + assembly_constituency_id)
├── models/                    # SQLAlchemy ORM models
├── schemas/                   # Pydantic request/response schemas
├── core/
│   ├── config.py              # Settings from .env
│   ├── security.py            # JWT + bcrypt
│   ├── storage.py             # Supabase Storage upload
│   ├── image_processing.py    # crop_rois() — active; enhance_* — unused
│   ├── smart_parser.py        # 3-pass HTML+plain-text OCR output parser
│   ├── constituency_resolver.py  # Fuzzy match OCR text → canonical Hindi name
│   └── chandra_ocr_engine.py  # ChandraOCR VLM wrapper with thread timeout
├── workers/
│   └── ocr_worker.py          # Polling worker (daemon thread or standalone)
├── db/
│   ├── base.py                # SQLAlchemy Base
│   ├── base_model.py          # All model imports (for create_all)
│   └── session.py             # SessionLocal factory
└── utils/
    ├── exceptions.py          # AppException → standardized error JSON
    └── success_response.py    # Standardized success JSON wrapper
```

**Request flow:** Route → Pydantic schema validation → Service / Repo → DB

**No Alembic migrations** — `Base.metadata.create_all()` runs at startup. Schema changes take effect immediately on the next server restart.

### Worker Architecture

The OCR worker runs as a **daemon thread** started in the FastAPI `startup` event. It can also run standalone:

```bash
python -m app.workers.ocr_worker
```

- Polls every **3 seconds** for pending jobs
- Pre-loads the ChandraOCR model at startup (`chandra_warmup()`) to avoid cold-start delay on first job
- Graceful shutdown via `threading.Event` — catches SIGTERM/SIGINT
- Signal handlers wrapped in `try/except` so they silently no-op when running as a daemon thread (signals only work on the main thread)

---

## OCR Pipeline

```
POST /ocr/upload
      │
      ├─ HEIC/HEIF → JPEG (quality 95)
      ├─ Upload to Supabase Storage: {user_id}/{job_id}/{uuid}.{ext}
      └─ Create Job row (status=pending, is_cropped flag)

Worker picks up job (every 3s)
      │
      ├─ Download image from Supabase Storage
      │
      ├─ Image preprocessing
      │     ├─ is_cropped=True  → use as-is
      │     └─ is_cropped=False → crop_rois():
      │           top_left     = image[0:25%h, 0:60%w]   (structured data)
      │           form_section = image[25%h:55%h, :]     (mobile/form area)
      │           → resize both to same width → vconcat
      │
      ├─ ChandraOCR (datalab-to/chandra-ocr-2)
      │     prompt_type="ocr_layout" → Markdown/HTML output
      │     timeout: 300s GPU / 600s CPU
      │
      ├─ parse_smart() — 3-pass parser:
      │     Pass 1: plain text before first <table> (Pattern B cards)
      │     Pass 2: <th>/<td> header cells (Pattern A cards)
      │     Pass 3: adjacent label→value cell pairs (mobile, district, state)
      │
      ├─ resolve_constituency()
      │     rapidfuzz 65% threshold → canonical Hindi name + district from DB
      │     ambiguous/no match → value=null, confidence=0.0
      │
      └─ Save result: { raw_text, parsed } or failed + error_message
```

**Completed job response:**
```json
{
  "job_id": "...",
  "status": "completed",
  "data": {
    "raw_text": "...",
    "parsed": {
      "name":                  { "value": "राम कुमार",    "confidence": 0.95 },
      "epic":                  { "value": "ABC1234567",   "confidence": 0.95 },
      "mobile":                { "value": "9876543210",   "confidence": 0.95 },
      "serial_number":         { "value": "123",          "confidence": 0.97 },
      "part_number_and_name":  { "value": "45 - ...",     "confidence": 0.75 },
      "assembly_constituency": { "value": "लखनऊ",         "confidence": 0.99 },
      "district":              { "value": "लखनऊ",         "confidence": 0.99 },
      "state":                 { "value": "उत्तर प्रदेश", "confidence": 0.99 },
      "address":               { "value": "...",          "confidence": 0.80 }
    }
  }
}
```

---

## Role Hierarchy

| Role | Data Scope | Required at Registration |
|------|-----------|--------------------------|
| `superadmin` | All voters in DB | — |
| `district` | All voters in their district | `district_id` |
| `mandal` | All voters in their mandal | `mandal_id` |
| `constituency` | All voters in their constituency | `constituency_id` |
| `booth` | Only voters they uploaded | `booth_id` |

`auth_service.register_user()` validates the provided ID against the DB and auto-populates all parent hierarchy fields.

---

## Local Development Setup

### Prerequisites

- Python 3.11
- PostgreSQL (Supabase project)
- NVIDIA GPU recommended (RTX 3000+ series, ≥16 GB VRAM)

### Steps

```bash
# 1. Clone and create venv
git clone <repo-url>
cd eci_ocr_backend
python3.11 -m venv venv
source venv/bin/activate

# 2. Install base dependencies
pip install -r requirements.txt

# 3. Install OCR library (not in requirements.txt)
pip install chandra-ocr[hf]

# 4. Install CUDA PyTorch — match your local CUDA version
#    RTX 4060 / CUDA 12.8:
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
#    CUDA 12.6:
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
#    CPU only (slow — ~10 min per job):
pip install torch torchvision

# 5. Create .env with your credentials (see Environment Variables section)

# 6. Start the server (worker starts automatically)
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Verify GPU is working

```bash
python -c "import torch; print(torch.version.cuda); print(torch.cuda.is_available())"
# Must print True. If False, reinstall PyTorch with the correct CUDA version.
```

On first startup the model (~10.6 GB) downloads to `~/.cache/huggingface` (or `$HF_HOME`). Watch for:
```
✅ ChandraOCR loaded
```

---

## Environment Variables

Create a `.env` file in the project root:

```env
# Database
DATABASE_URL=postgresql://user:password@host:5432/dbname

# JWT
SECRET_KEY=your-secret-key-here
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=360

# Supabase Storage
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key

# HuggingFace cache (strongly recommended on RunPod/cloud — point to network volume)
HF_HOME=/runpod-volume/huggingface
```

> `HF_TOKEN` is not required — `datalab-to/chandra-ocr-2` is a public model (Apache 2.0).
> `SARVAM_*` and `ANTHROPIC_API_KEY` appear in config.py but are unused by any active code path.

---

## API Reference

### Auth

| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/register` | Create user — `role` + matching hierarchy ID required |
| POST | `/auth/login` | Returns `access_token` |
| GET | `/auth/getUser` | Current authenticated user info |

### OCR

| Method | Path | Description |
|--------|------|-------------|
| POST | `/ocr/upload` | Multipart upload — `file` + optional `isCropped` bool |
| GET | `/ocr/result/{job_id}` | Poll for result |

**Upload accepted formats:** jpeg, png, jpg, webp, svg, heic, heif — max **10 MB**

**Polling strategy:** Call every 2–3 seconds until status is `completed` or `failed`.

### Voter

| Method | Path | Description |
|--------|------|-------------|
| GET | `/voter/getVoters?page=&limit=` | Paginated list (role-scoped) |
| GET | `/voter/getVoters?epic=` | EPIC search (booth-scoped for booth role) |
| POST | `/voter/save` | Create voter — send `assembly_constituency_name` (Hindi or English) |
| PUT | `/voter/{voter_id}?ac_id=` | Update voter |
| DELETE | `/voter/{voter_id}?ac_id=` | Delete voter |
| GET | `/voter/count` | Total voter count (global, not role-filtered) |
| GET | `/voter/export` | Download CSV — filters: `name`, `mobile`, `epic`, `assembly_constituency_id`, `district_id` |

> No `GET /voter/{voter_id}` single-lookup endpoint exists.
> `PUT` and `DELETE` need both `voter_id` (path) and `ac_id` (query param) because the PK is composite.
> `GET /voter/getVoters` returns HTTP **204** (no body) when no results match.

---

## Deployment

### Render.com (CPU only — low volume)

`render.yaml` defines a single web service. The OCR worker runs as an embedded daemon thread.

- **Build:** `pip install -r requirements.txt`
- **Start:** `uvicorn app.main:app --host 0.0.0.0 --port 10000`

> Render has no GPU instances. OCR on CPU takes 5–10 minutes per job. Add `chandra-ocr[hf]` to `requirements.txt` for Render builds.

---

### RunPod (Recommended for GPU)

1. Create a **GPU pod** with a Network Volume attached
2. Expose port `10000` in pod settings

```bash
# Point HF cache to network volume — do this BEFORE starting the server
export HF_HOME=/runpod-volume/huggingface

# Setup (first time only)
cd /root
git clone <repo-url> eci_ocr_backend && cd eci_ocr_backend
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pip install chandra-ocr[hf]

# Match PyTorch to your pod's CUDA driver (check with nvidia-smi)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126 --force-reinstall

# Create .env then start
HF_HOME=/runpod-volume/huggingface python -m uvicorn app.main:app --host 0.0.0.0 --port 10000
```

**CUDA mismatch fix** — if `torch.cuda.is_available()` is `False`:
```bash
nvidia-smi                          # note the CUDA version on top right
python -c "import torch; print(torch.version.cuda)"   # what PyTorch expects
# Reinstall with --force-reinstall for the correct cu version
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126 --force-reinstall
```

---

### AWS EC2 (Production GPU)

**Recommended instance: `g6.xlarge`** — L4 GPU (24 GB VRAM), ~$0.81/hr on-demand, available in `ap-south-1` (Mumbai).

| Instance | GPU | VRAM | On-Demand |
|----------|-----|------|-----------|
| `g6.xlarge` | L4 | 24 GB | ~$0.81/hr |
| `g5.xlarge` | A10G | 24 GB | ~$1.01/hr |
| `p3.2xlarge` | V100 | 16 GB | ~$3.06/hr |

**Setup checklist:**
- **AMI:** "Deep Learning Base OSS Nvidia Driver GPU AMI" (AWS Marketplace) — ships with CUDA + PyTorch pre-installed
- **Storage:** ≥ 50 GB EBS gp3 (model ~10.6 GB + OS + venv)
- **Security group:** Inbound TCP 10000 (prod) / 8000 (dev)
- **Elastic IP:** Assign one — prevents IP change on stop/start

```bash
git clone <repo-url> && cd eci_ocr_backend
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt && pip install chandra-ocr[hf]
# Verify GPU (AMI has CUDA pre-installed)
python -c "import torch; print(torch.cuda.is_available())"
# Create .env then:
uvicorn app.main:app --host 0.0.0.0 --port 10000
```

---

## Hardware Guide

The model is `datalab-to/chandra-ocr-2` — **5B parameters, BF16 ≈ 10.6 GB VRAM**. Any GPU with less than 12 GB VRAM cannot run this model.

### GPU performance reference

| GPU | VRAM | Est. Inference Time | Notes |
|-----|------|-------------------|-------|
| H100 SXM | 80 GB | 3–8s | Fastest available |
| A100 SXM 80GB | 80 GB | 5–10s | Excellent |
| A100 PCIe 40GB | 40 GB | 8–15s | Good |
| RTX 4090 | 24 GB | 10–20s | Best consumer option |
| L4 / A10G | 24 GB | 15–25s | AWS g6/g5 recommended |
| RTX 4060 Ti 16GB | 16 GB | 25–40s | Minimum viable |
| RTX 3060 12GB | 12 GB | ❌ | Insufficient VRAM |
| CPU only | — | 5–10 min | Last resort |

### RunPod pod recommendations

| GPU | VRAM | RunPod Cost | Verdict |
|-----|------|------------|---------|
| A100 SXM | 80 GB | ~$2.49/hr | Production |
| H100 PCIe | 80 GB | ~$2.99/hr | Best speed |
| RTX 4090 | 24 GB | ~$0.74/hr | Dev / medium volume |
| A40 | 48 GB | ~$0.76/hr | Good balance |
| RTX 3090 | 24 GB | ~$0.44/hr | Budget |

**Always attach a Network Volume** — without it, the 10.6 GB model re-downloads on every pod restart.

---

## Performance Optimization

### 1. Install fast-path libraries (biggest single win)

Without these, the model logs:
```
The fast path is not available because one of the required library is not installed.
Falling back to torch implementation.
```

```bash
pip install causal-conv1d flash-linear-attention
```

Restart the server after installing. The warning should disappear.

### 2. Reduce max_new_tokens

In `app/core/chandra_ocr_engine.py` line 63, default is `2048`. A voter card is ~300–500 tokens:

```python
result = generate_hf(batch, _model, max_new_tokens=512)[0]
```

### 3. Persist model weights to a network volume

```bash
export HF_HOME=/runpod-volume/huggingface
# or prepend to start command:
HF_HOME=/runpod-volume/huggingface python -m uvicorn app.main:app --host 0.0.0.0 --port 10000
```

Without this, every container restart re-downloads ~10.6 GB.

### 4. torch.compile (GPU only, ~20–30% speedup)

In `_load()` in `chandra_ocr_engine.py`, after `m.eval()`:

```python
if torch.cuda.is_available():
    m = torch.compile(m, mode="reduce-overhead")
```

First inference after compile is slower (compilation). Subsequent inferences benefit from the speedup.

### 5. Monitor GPU during inference

```bash
watch -n 1 nvidia-smi
```

Confirms GPU utilization and that VRAM is fully used.

---

## Important Commands Reference

```bash
# ── Environment ──────────────────────────────────────────────────────────────
source venv/bin/activate
deactivate

# ── Install ──────────────────────────────────────────────────────────────────
pip install -r requirements.txt
pip install chandra-ocr[hf]

# ── PyTorch CUDA builds (pick one matching your driver) ──────────────────────
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
pip install torch torchvision                                                    # CPU only

# Force reinstall (bypasses pip "already satisfied" on wrong CUDA variant)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126 --force-reinstall

# ── Server ───────────────────────────────────────────────────────────────────
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000             # dev
python -m uvicorn app.main:app --host 0.0.0.0 --port 10000                     # prod
HF_HOME=/runpod-volume/huggingface python -m uvicorn app.main:app --host 0.0.0.0 --port 10000

# ── Worker standalone ────────────────────────────────────────────────────────
python -m app.workers.ocr_worker

# ── Diagnostics ──────────────────────────────────────────────────────────────
nvidia-smi
watch -n 1 nvidia-smi
python -c "import torch; print(torch.__version__); print(torch.version.cuda); print(torch.cuda.is_available())"

# ── Performance extras ───────────────────────────────────────────────────────
pip install causal-conv1d flash-linear-attention
```

---

## Known Gotchas

**CUDA driver vs PyTorch mismatch — GPU not used**
`torch.cuda.is_available()` returns `False` when PyTorch was compiled for a newer CUDA than the installed driver. `pip install` says "already satisfied" even when the CUDA variant is wrong. Always use `--force-reinstall` and `--index-url` together.

**`uvicorn: command not found`**
The venv is not activated or uvicorn isn't in `PATH`. Use `python -m uvicorn ...` — works without venv activation.

**Model re-downloads on every RunPod restart**
Set `HF_HOME` to a path on the Network Volume before starting the server. Container disk is ephemeral — anything not on the volume is lost on pod restart.

**Constituency resolver returns null**
`resolve_constituency()` uses 65% fuzzy threshold. Heavy OCR corruption on the constituency field causes the resolver to return `null` + `confidence=0.0` rather than guess wrong. User needs to retake the image with the constituency field clearly visible.

**Voter update/delete silently requires ac_id**
The voter table has composite PK `(id, assembly_constituency_id)`. `PUT /voter/{id}` and `DELETE /voter/{id}` both require `?ac_id=` as a query param — omitting it will fail or hit the wrong record.

**Parsing returns empty fields**
`parse_smart()` handles two voter card layouts. If the OCR output format changes (e.g. after a model update), re-test both Pattern A (HTML table) and Pattern B (plain text) outputs against real card scans.

**CSV exports are local-filesystem only**
`exports/` directory is on the server's local disk. Not suitable for multi-instance deployments.

---

## API Testing

Import `eci_ocr_backend.postman_collection.json` into Postman. Set these collection variables:

| Variable | Example |
|----------|---------|
| `base_url` | `http://localhost:8000` |
| `username` | your registered username |
| `password` | your password |

The collection handles login and token refresh automatically.
