from supabase import create_client, Client
from app.core.config import settings
import uuid
import mimetypes

# Initialize client
supabase: Client = create_client(
    settings.SUPABASE_URL,
    settings.SUPABASE_SERVICE_ROLE_KEY
)

BUCKET_NAME = "ocr-images"

def upload_image(file_bytes: bytes, user_id: str, job_id: str, filename: str):
    # generate unique filename
    ext = filename.split(".")[-1]
    unique_name = f"{uuid.uuid4()}.{ext}"

    # final path
    file_path = f"{user_id}/{job_id}/{unique_name}"

    content_type, _ = mimetypes.guess_type(filename)

    # upload
    response = supabase.storage.from_(BUCKET_NAME).upload(
        path=file_path,
        file=file_bytes,
        file_options={"content-type": content_type or "application/octet-stream"}
    )

    # optional: check error
    if hasattr(response, "error") and response.error:
        raise Exception(f"Upload failed: {response.error}")

    return file_path