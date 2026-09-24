"""Output directory layout and JSON persistence for pipeline artifacts."""

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from tutorial_ai.utils.text import slugify


@dataclass(frozen=True)
class ProjectPaths:
    """output/<project>/... - one directory per tutorial project."""

    root: Path

    @classmethod
    def for_project(cls, output_directory: Path, project_name: str) -> "ProjectPaths":
        return cls(root=output_directory / slugify(project_name))

    @property
    def repository_analysis(self) -> Path:
        return self.root / "repository_analysis.json"

    @property
    def repository_context(self) -> Path:
        """The exact repository context sent to the AI - kept for inspection and debugging."""
        return self.root / "repository_context.md"

    @property
    def tutorial_plan(self) -> Path:
        return self.root / "tutorial_plan.json"

    @property
    def script(self) -> Path:
        return self.root / "script.json"

    @property
    def scenes(self) -> Path:
        return self.root / "scenes.json"

    @property
    def narration(self) -> Path:
        return self.root / "narration.json"

    @property
    def timeline(self) -> Path:
        return self.root / "timeline.json"

    @property
    def rehearsal(self) -> Path:
        return self.root / "rehearsal.json"

    @property
    def recordings(self) -> Path:
        return self.root / "recordings.json"

    @property
    def audio_dir(self) -> Path:
        return self.root / "audio"

    @property
    def scenes_dir(self) -> Path:
        return self.root / "scenes"

    @property
    def final_video(self) -> Path:
        return self.root / "tutorial.mp4"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    def ensure(self) -> "ProjectPaths":
        self.root.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        return self


def write_text_atomic(path: Path, content: str) -> None:
    """Write via a temp file + rename so an interrupted run never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save_model(model: BaseModel, path: Path) -> None:
    write_text_atomic(path, model.model_dump_json(indent=2) + "\n")


def load_model[T: BaseModel](path: Path, model_type: type[T]) -> T:
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))
