import os
import signal
import threading
import cv2
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.models.job import Job
from app.db.base_model import *

from app.core.image_processing import (
    download_image,
    crop_rois,
)

# ---------------------------------------------------------------------------
# OCR engines — swap active engine by toggling the imports below.
#
# Engine           | Output          | Parser
# ---------------- | --------------- | ----------------------------
# SmolVLM-500M     | HTML/Markdown   | parse_smart(text)  (no db)
# MiniCPM-V        | HTML/Markdown   | parse_smart(text)  (no db)  ← ACTIVE
# PaddleOCR-VL     | HTML/Markdown   | parse_smart(text)  (no db)
# Sarvam           | HTML            | parse_smart(text)  (no db)
# PaddleOCR        | plain text      | parse_ocr_text(text, db)
# ---------------------------------------------------------------------------

# -- SmolVLM-500M (local testing — low VRAM, weaker Hindi OCR) --
# from app.core.smolvlm_engine import run_smolvlm

# -- ACTIVE: MiniCPM-V-2_6 (8B VLM, strong multilingual OCR incl. Hindi) --
from app.core.minicpm_v_engine import run_minicpm_v

# -- PaddleOCR-VL (0.9B VLM, 109 langs including Devanagari/Hindi) --
# from app.core.paddleocr_vl_engine import run_paddleocr_vl

# -- Sarvam OCR + HTML-aware smart parser (production) --
# from app.core.sarvam import run_sarvam
# from app.core.smart_parser import parse_smart

# -- Classic PaddleOCR mobile models (local testing) --
# from app.core.paddleocr_engine import run_paddleocr


POLL_INTERVAL = 3  # seconds

# Event set by signal handlers to stop the worker loop cleanly
_stop_event = threading.Event()


def _handle_shutdown(signum, frame):
    print(f"\n🛑 Worker received signal {signum} — shutting down cleanly...")
    _stop_event.set()


def process_job(job: Job, db: Session):
    print(f"🚀 Processing job: {job.id}")

    try:
        # 1. Download image
        print("⬇️ Downloading image...")
        image = download_image(job.image_path)
        print("✅ Image downloaded")

        # 2. Process image
        if job.is_cropped:
            print("🟢 Cropped image → using as-is")
            processed = image
        else:
            print("🟡 Not cropped → ROI processing")

            top_left, form_section = crop_rois(image)

            w = max(top_left.shape[1], form_section.shape[1])
            top_left_resized = cv2.resize(top_left, (w, top_left.shape[0]))
            form_section_resized = cv2.resize(form_section, (w, form_section.shape[0]))

            processed = cv2.vconcat([top_left_resized, form_section_resized])

        # 3. Run MiniCPM-V
        print("🧠 Calling MiniCPM-V...")
        ocr_text = run_minicpm_v(processed)
        print("📄 OCR text received")

        # 4. Parse disabled — saving raw output only for inspection
        # Uncomment once output format is confirmed to match smart_parser expectations
        # parsed = parse_smart(ocr_text)
        # if parsed.get("name", {}).get("value"):
        #     print("✅ Parser succeeded")
        # else:
        #     print("⚠️ Parser: name not found")
        #     parsed = {}

        # 5. Save result (raw only)
        job.status = "completed"
        job.result = {
            "raw_text": ocr_text,
        }

    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        job.status = "failed"
        job.error_message = str(e)

    db.commit()

    print(f"🏁 Job finished: {job.id} → {job.status}")


def worker():
    # Force line-buffered stdout so logs appear immediately in the terminal
    # even when running as a child process (multiprocessing.Process buffers
    # output by default)
    import sys
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

    print("🚀 Worker started...", flush=True)

    # Register signal handlers so PaddlePaddle's C++ backend gets a chance
    # to release resources before the process exits — prevents crashes on
    # Ctrl+C or terminal close on macOS
    try:
        signal.signal(signal.SIGINT, _handle_shutdown)
        signal.signal(signal.SIGTERM, _handle_shutdown)
    except (OSError, ValueError):
        pass

    while not _stop_event.is_set():
        db: Session = SessionLocal()

        try:
            job = (
                db.query(Job)
                .filter(Job.status == "pending")
                .order_by(Job.created_at.asc())
                .first()
            )

            if not job:
                print("😴 No pending jobs...")
                _stop_event.wait(timeout=POLL_INTERVAL)
                continue

            job.status = "processing"
            db.commit()
            db.refresh(job)

            try:
                process_job(job, db)

            except Exception as e:
                print(f"❌ Error processing job {job.id}: {str(e)}")
                db.rollback()
                job.status = "failed"
                job.error_message = str(e)
                db.commit()

        finally:
            db.close()

        _stop_event.wait(timeout=POLL_INTERVAL)

    print("✅ Worker stopped cleanly")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.set_start_method("spawn", force=True)  # required on macOS
    worker()