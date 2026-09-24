from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base for all domain models: unknown fields are rejected instead of silently dropped."""

    model_config = ConfigDict(extra="forbid")


class Difficulty(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


def validate_workspace_path(value: str) -> str:
    """A path relative to the tutorial workspace that cannot escape it. Normalises './x' to 'x'."""
    from pathlib import PurePosixPath

    stripped = value.strip()
    while stripped.startswith("./"):
        stripped = stripped[2:]
    path = PurePosixPath(stripped)
    if not stripped or path.is_absolute() or ".." in path.parts or stripped.startswith("~"):
        raise ValueError(f"path must be relative to the workspace and not escape it: {value!r}")
    return stripped
