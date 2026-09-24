"""Recording models."""

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from tutorial_ai.models.base import StrictModel


class RecordingStatus(StrEnum):
    PENDING = "PENDING"
    RECORDING = "RECORDING"
    SUCCESS = "SUCCESS"
    RETRY = "RETRY"
    REPAIR = "REPAIR"
    PAUSED = "PAUSED"
    ABORTED = "ABORTED"


class RecordingJob(StrictModel):
    project_name: str
    scene_id: str
    output_path: str
    width: int = 1920
    height: int = 1080
    fps: int = Field(default=30, ge=1, le=60)


class RecordedSegment(StrictModel):
    """Where a narration clip actually starts in the recording (seconds from recording start)."""

    segment_id: str
    start_seconds: float = Field(ge=0)


class RecordedBeat(StrictModel):
    beat_id: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)


class RecordingResult(StrictModel):
    scene_id: str
    status: RecordingStatus
    video_path: str | None = None  # relative to the project directory (with narration muxed)
    raw_video_path: str | None = None  # the silent screen capture
    duration_seconds: float | None = None
    recorded_at: datetime | None = None
    beats: list[RecordedBeat] = Field(default_factory=list)
    segments: list[RecordedSegment] = Field(default_factory=list)
    error: str | None = None


class RecordingsManifest(StrictModel):
    """recordings.json - one entry per scene, replaced when a scene is re-recorded."""

    results: list[RecordingResult] = Field(default_factory=list)
    final_video_path: str | None = None
    final_duration_seconds: float | None = None

    def get(self, scene_id: str) -> RecordingResult | None:
        return next((r for r in self.results if r.scene_id == scene_id), None)

    def put(self, result: RecordingResult) -> None:
        self.results = [r for r in self.results if r.scene_id != result.scene_id] + [result]
        self.results.sort(key=lambda r: r.scene_id)


class VideoProject(StrictModel):
    """Index of every artifact produced for one tutorial, stored in the project output dir."""

    name: str
    output_dir: str
    repository_analysis_path: str | None = None
    tutorial_plan_path: str | None = None
    script_path: str | None = None
    timeline_path: str | None = None
    recordings: list[RecordingResult] = Field(default_factory=list)
    final_video_path: str | None = None
