import re


def slugify(value: str) -> str:
    """Filesystem-safe project name: 'FastAPI File-Upload!' -> 'fastapi-file-upload'."""
    slug = re.sub(r"[^a-z0-9._-]+", "-", value.strip().lower()).strip("-.")
    return slug or "project"


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 characters per token) used for budgeting, not billing."""
    return (len(text) + 3) // 4
