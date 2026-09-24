"""Timeline models (produced in Phase 4 from scenes + measured audio durations)."""

from typing import Literal

from pydantic import Field, model_validator

from tutorial_ai.models.base import StrictModel


class AudioSegment(StrictModel):
    segment_id: str
    scene_id: str
    path: str
    duration_seconds: float = Field(gt=0)
    sample_rate: int | None = None


class TimelineEvent(StrictModel):
    time_seconds: float = Field(ge=0, description="Offset from the start of the scene.")
    duration_seconds: float = Field(ge=0)
    kind: Literal["narration", "action"]
    ref_id: str = Field(description="Narration segment id or action id.")
    description: str = ""


class SceneTimeline(StrictModel):
    scene_id: str
    events: list[TimelineEvent]
    duration_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def _events_are_sorted_and_fit(self) -> "SceneTimeline":
        times = [e.time_seconds for e in self.events]
        if times != sorted(times):
            raise ValueError(f"events in scene {self.scene_id} must be sorted by time")
        end = max((e.time_seconds + e.duration_seconds for e in self.events), default=0.0)
        if end > self.duration_seconds + 1e-6:
            raise ValueError(
                f"scene {self.scene_id} events end at {end:.2f}s, after the scene "
                f"duration {self.duration_seconds:.2f}s"
            )
        return self


class Timeline(StrictModel):
    project_name: str
    scenes: list[SceneTimeline]

    @property
    def total_duration_seconds(self) -> float:
        return sum(s.duration_seconds for s in self.scenes)
