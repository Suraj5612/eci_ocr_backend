from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status
from sqlalchemy.orm import Session
from app.api.deps import get_db, get_current_user
from app.schemas.ocr import UploadResponse
from app.repositories.job_repo import create_job
from app.core.storage import upload_image
import uuid


router = APIRouter()


# 🔥 Allowed types
ALLOWED_TYPES = ["image/jpeg", "image/png", "image/jpg", "image/webp"]

# 🔥 Max size (10MB)
MAX_FILE_SIZE = 10 * 1024 * 1024


@router.post("/upload", response_model=UploadResponse)
async def upload_ocr(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
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
        file_path = upload_image(
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
        image_path=file_path
    )

    return {
        "job_id": job.id,
        "status": job.status
    }