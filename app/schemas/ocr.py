from pydantic import BaseModel


class UploadResponse(BaseModel):
    isCropped: bool
    job_id: str
    status: str