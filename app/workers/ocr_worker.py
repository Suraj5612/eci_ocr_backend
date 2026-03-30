import time
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.models.job import Job
from app.db.base_model import * 
from app.core.image_processing import (
    download_image,
    crop_rois,
    normalize_lighting,
    save_debug_images,
    enhance_printed,
    enhance_handwritten,
    enhance_cropped,
    remove_shadow
)


POLL_INTERVAL = 3  # seconds


def process_job(job: Job, db: Session):
    print(f"Processing job: {job.id}")

    try:
        # 1. Download image
        print("Downloading image...")
        image = download_image(job.image_path)
        print("Image downloaded")

        # 2. Decide flow based on fla

        if job.is_cropped:
            print("🟢 Cropped image → minimal enhancement")

            enhanced = enhance_cropped(image)

            top_left = enhanced
            form_section = enhanced

        else:
            print("🟡 Not cropped → ROI + specialized enhancement")

            top_left, form_section = crop_rois(image)

            # 🔥 remove shadow ONLY for printed
            top_left = enhance_cropped(top_left)

            # 🔥 light enhancement for handwritten
            form_section = enhance_cropped(form_section)

                # 3. Save debug images
        print("Saving debug images...")
        save_debug_images(job.id, top_left, form_section)
        print("Debug images saved")

        job.status = "completed"
        job.result = {
            "message": "ROI step done",
            "is_cropped": job.is_cropped
        }

    except Exception as e:
        print(f"ERROR: {str(e)}")
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
            # get one pending job
            job = (
                db.query(Job)
                .filter(Job.status == "pending")
                .order_by(Job.created_at.asc())
                .first()
            )

            if not job:
                print("No pending jobs...")
                time.sleep(POLL_INTERVAL)
                continue

            # mark as processing
            job.status = "processing"
            db.commit()
            db.refresh(job)

            try:
                process_job(job, db)

            except Exception as e:
                print(f"Error processing job {job.id}: {str(e)}")

                job.status = "failed"
                job.error = str(e)

                db.commit()

        finally:
            db.close()

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    worker()