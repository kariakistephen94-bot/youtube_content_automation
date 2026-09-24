from fastapi import APIRouter, UploadFile

from app.schemas.files import UploadResponse
from app.services.files import save_upload

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("", response_model=UploadResponse, status_code=201)
async def upload_file(file: UploadFile) -> UploadResponse:
    stored_as, size = await save_upload(file)
    return UploadResponse(
        filename=file.filename or stored_as,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=size,
        stored_as=stored_as,
    )
