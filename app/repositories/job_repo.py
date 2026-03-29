from sqlalchemy.orm import Session
from app.models.job import Job


def create_job(db: Session, user_id: str, image_path: str):
    job = Job(
        user_id=user_id,
        image_path=image_path,
        status="pending"
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_job_by_id(db: Session, job_id: str):
    return db.query(Job).filter(Job.id == job_id).first()


def update_job_status(
    db: Session,
    job_id: str,
    status: str,
    result: dict = None,
    error_message: str = None
):
    job = get_job_by_id(db, job_id)

    if job:
        job.status = status
        job.result = result
        job.error_message = error_message

        db.commit()
        db.refresh(job)

    return job