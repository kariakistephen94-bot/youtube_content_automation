from pydantic import BaseModel


class UploadResponse(BaseModel):
    filename: str
    content_type: str
    size_bytes: int
    stored_as: str
