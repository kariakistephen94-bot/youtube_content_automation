import os
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

ALLOWED_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "5")) * 1024 * 1024


def validate_upload(file: UploadFile) -> str:
    """Return the file extension to store the upload with, or raise a 400."""
    extension = ALLOWED_CONTENT_TYPES.get(file.content_type or "")
    if extension is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.content_type}. Upload a PDF, PNG or JPEG.",
        )
    return extension


async def save_upload(file: UploadFile) -> tuple[str, int]:
    """Save the upload under a random name. Returns (stored name, size in bytes)."""
    extension = validate_upload(file)
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is too large.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{extension}"
    (UPLOAD_DIR / stored_name).write_bytes(contents)
    return stored_name, len(contents)
