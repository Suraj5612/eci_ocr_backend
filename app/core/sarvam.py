import requests
import time
import cv2
import zipfile
import tempfile
import io
from app.core.config import settings

BASE_URL = settings.SARVAM_BASE_URL
API_KEY = settings.SARVAM_API_KEY


def create_job():
    res = requests.post(
        f"{BASE_URL}/doc-digitization/job/v1",
        headers={
            "api-subscription-key": API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "job_parameters": {
                "language": "hi-IN",
                "output_format": "md",
            }
        },
    )

    if res.status_code not in [200, 202]:
        raise Exception(f"Job creation failed: {res.text}")

    return res.json()["job_id"]


def create_zip(image):
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")

    _, buffer = cv2.imencode(".jpg", image)

    with zipfile.ZipFile(temp.name, "w") as z:
        z.writestr("page_1.jpg", buffer.tobytes())

    return temp.name


def get_upload_url(job_id):
    res = requests.post(
        f"{BASE_URL}/doc-digitization/job/v1/upload-files",
        headers={"api-subscription-key": API_KEY},
        json={
            "job_id": job_id,
            "files": ["input.zip"],
        },
    )

    data = res.json()
    return list(data["upload_urls"].values())[0]["file_url"]


def upload_zip(upload_url, zip_path):
    with open(zip_path, "rb") as f:
        res = requests.put(
            upload_url,
            headers={"x-ms-blob-type": "BlockBlob"},
            data=f,
        )

    if res.status_code not in [200, 201]:
        raise Exception(f"Upload failed: {res.text}")


def start_job(job_id):
    res = requests.post(
        f"{BASE_URL}/doc-digitization/job/v1/{job_id}/start",
        headers={"api-subscription-key": API_KEY},
    )

    if res.status_code not in [200, 202]:
        raise Exception(f"Start failed: {res.text}")


def wait_for_completion(job_id):
    for _ in range(30):
        time.sleep(4)

        res = requests.get(
            f"{BASE_URL}/doc-digitization/job/v1/{job_id}/status",
            headers={"api-subscription-key": API_KEY},
        )

        if res.status_code != 200:
            continue

        state = res.json().get("job_state")

        print(f"Sarvam status: {state}")

        if state in ["Completed", "PartiallyCompleted"]:
            return
        if state == "Failed":
            raise Exception("OCR failed")

    raise Exception("OCR timeout")


def get_download_url(job_id):
    res = requests.post(
        f"{BASE_URL}/doc-digitization/job/v1/{job_id}/download-files",
        headers={"api-subscription-key": API_KEY},
    )

    data = res.json()
    return list(data["download_urls"].values())[0]["file_url"]


def extract_text(download_url):
    res = requests.get(download_url)

    if res.status_code != 200:
        raise Exception("Download failed")

    z = zipfile.ZipFile(io.BytesIO(res.content))

    for name in z.namelist():
        if name.endswith(".md"):
            text = z.read(name).decode("utf-8")

            # clean markdown images
            return text.replace("![Image]", "").strip()

    raise Exception("No text found")


def run_sarvam(image):
    print("🚀 Starting Sarvam OCR...")

    job_id = create_job()
    print(f"Job ID: {job_id}")

    zip_path = create_zip(image)

    upload_url = get_upload_url(job_id)
    upload_zip(upload_url, zip_path)

    start_job(job_id)
    wait_for_completion(job_id)

    download_url = get_download_url(job_id)

    text = extract_text(download_url)

    print("✅ OCR Completed")

    return text