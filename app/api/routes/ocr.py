from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status
from fastapi import Form
from sqlalchemy.orm import Session
from app.api.deps import get_db, get_current_user
from app.schemas.ocr import UploadResponse
from app.utils.exceptions import AppException
from app.repositories.job_repo import create_job, get_job_by_id
from app.core.storage import upload_image as upload_to_storage
import uuid
import io
from app.models.user import User


router = APIRouter()

ALLOWED_TYPES = [
    "image/jpeg", "image/jpg", "image/png", "image/webp",
    "image/svg+xml", "image/heic", "image/heif",
]

HEIC_TYPES = {"image/heic", "image/heif"}

MAX_FILE_SIZE = 10 * 1024 * 1024


def _convert_heic_to_jpeg(data: bytes) -> bytes:
    import pillow_heif
    from PIL import Image

    pillow_heif.register_heif_opener()
    img = Image.open(io.BytesIO(data))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=95)
    return buf.getvalue()


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

    filename = file.filename

    if file.content_type in HEIC_TYPES:
        try:
            file_bytes = _convert_heic_to_jpeg(file_bytes)
            filename = f"{uuid.uuid4()}.jpg"
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Failed to convert HEIC image: {str(e)}"
            )

    job_id = str(uuid.uuid4())

    try:
        file_path = upload_to_storage(
            file_bytes=file_bytes,
            user_id=current_user.id,
            job_id=job_id,
            filename=filename
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

import os
import json
from datetime import datetime

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
