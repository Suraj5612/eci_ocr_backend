from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status
from fastapi import Form
from sqlalchemy.orm import Session
from app.api.deps import get_db, get_current_user
from app.schemas.ocr import UploadResponse
from app.utils.exceptions import AppException
from app.repositories.job_repo import create_job, get_job_by_id
from app.core.storage import upload_image as upload_to_storage
import uuid
from app.models.user import User


router = APIRouter()

ALLOWED_TYPES = ["image/jpeg", "image/png", "image/jpg", "image/webp"]

MAX_FILE_SIZE = 10 * 1024 * 1024

@router.post("/upload", response_model=UploadResponse)
async def upload_image(
    file: UploadFile = File(...),
    isCropped: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file type"
        )

    file_bytes = await file.read()

    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size exceeds 10MB limit"
        )

    job_id = str(uuid.uuid4())

    try:
        file_path = upload_to_storage(
            file_bytes=file_bytes,
            user_id=current_user.id,
            job_id=job_id,
            filename=file.filename
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Upload failed: {str(e)}"
        )

    job = create_job(
        db=db,
        user_id=current_user.id,
        image_path=file_path,
        is_cropped=isCropped
    )

    return {
        "job_id": job.id,
        "status": job.status,
        "isCropped": job.is_cropped
    }

@router.get("/result/{job_id}")
def get_ocr_result(
    job_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    job = get_job_by_id(db, job_id)

    if not job:
        raise AppException(
            status_code=404,
            code="JOB_NOT_FOUND",
            message="Job not found"
        )

    if job.status in ["pending", "processing"]:
        return {
            "job_id": job.id,
            "status": job.status
        }

    if job.status == "failed":
        return {
            "job_id": job.id,
            "status": "failed",
            "error": job.error_message
        }

    return {
        "job_id": job.id,
        "status": "completed",
        "data": job.result
    }
