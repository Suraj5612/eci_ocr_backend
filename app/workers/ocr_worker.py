import time
import cv2
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.models.job import Job
from app.db.base_model import * 

from app.core.image_processing import (
    download_image,
    crop_rois,
    save_debug_images,
    enhance_handwritten,
    enhance_cropped
)

# 🔥 import sarvam
from app.core.sarvam import run_sarvam
from app.core.parser import parse_ocr_text


POLL_INTERVAL = 3  # seconds


def process_job(job: Job, db: Session):
    print(f"🚀 Processing job: {job.id}")

    try:
        # 1. Download image
        print("⬇️ Downloading image...")
        image = download_image(job.image_path)
        print("✅ Image downloaded")

        # 2. Process image
        if job.is_cropped:
            print("🟢 Cropped image → enhancement")

            #processed = enhance_cropped(image)
            processed = image

            top_left = processed
            form_section = processed

        else:
            print("🟡 Not cropped → ROI processing")

            #top_left, form_section = crop_rois(image)

            # printed
            #top_left = enhance_cropped(top_left)

            # handwritten (you can later improve)
            #form_section = enhance_handwritten(form_section)

            # ensure same width
            #w = max(top_left.shape[1], form_section.shape[1])

            #top_left_resized = cv2.resize(top_left, (w, top_left.shape[0]))
            #form_section_resized = cv2.resize(form_section, (w, form_section.shape[0]))

            # ensure same type (grayscale)
            #if len(top_left_resized.shape) == 2:
                #top_left_resized = cv2.cvtColor(top_left_resized, cv2.COLOR_GRAY2BGR)

            #if len(form_section_resized.shape) == 2:
                #form_section_resized = cv2.cvtColor(form_section_resized, cv2.COLOR_GRAY2BGR)

            # concat
            #processed = cv2.vconcat([top_left_resized, form_section_resized])
            processed = image

        # 🔥 4. CALL SARVAM OCR
        print("🧠 Calling Sarvam OCR...")
        ocr_text = run_sarvam(processed)

        print("📄 OCR Text received")

        # 5. Save result
        job.status = "completed"
        parsed = parse_ocr_text(ocr_text, db)

        job.result = {
            "raw_text": ocr_text,
            "parsed": parsed
        }

    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        job.status = "failed"
        job.error = str(e)

    db.commit()
    db.refresh(job)

    print(f"🏁 Job finished: {job.id} → {job.status}")


def worker():
    print("🚀 Worker started...")

    while True:
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
                time.sleep(POLL_INTERVAL)
                continue

            # mark processing
            job.status = "processing"
            db.commit()
            db.refresh(job)

            try:
                process_job(job, db)

            except Exception as e:
                print(f"❌ Error processing job {job.id}: {str(e)}")
                db.rollback()
                job.status = "failed"
                job.error = str(e)
                db.commit()

        finally:
            db.close()

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    worker()