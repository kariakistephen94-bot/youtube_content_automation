"""Scene models: the unit of recording (one scene -> one scene_NNN.mp4)."""

from pydantic import Field, model_validator

from tutorial_ai.models.action import Action
from tutorial_ai.models.base import StrictModel
from tutorial_ai.models.script import ScriptBeat


class NarrationSegment(StrictModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    beat_id: str | None = Field(default=None, description="Script beat this narration belongs to.")
    estimated_duration_seconds: float = Field(gt=0)
    audio_path: str | None = None  # filled in by narration generation
    measured_duration_seconds: float | None = Field(default=None, gt=0)  # from the real audio
    start_offset_seconds: float | None = Field(default=None, ge=0)  # filled in by the timeline
    linked_actions: list[str] = Field(
        default_factory=list, description="Ids of actions performed while this segment plays."
    )


class ExpectedState(StrictModel):
    """What must be true in the tutorial workspace when the scene finishes."""

    files_exist: list[str] = Field(default_factory=list)
    running_processes: list[str] = Field(default_factory=list)
    application_url: str | None = None


class Scene(StrictModel):
    id: str = Field(min_length=1)
    title: str
    goal: str
    section_number: int | None = Field(default=None, ge=1)
    beats: list[ScriptBeat] = Field(
        default_factory=list, description="The script beats (what happens on screen) in this scene."
    )
    narration_segments: list[NarrationSegment] = Field(min_length=1)
    actions: list[Action] = Field(default_factory=list)
    estimated_duration_seconds: float = Field(gt=0)
    expected_state: ExpectedState = Field(default_factory=ExpectedState)
    files_involved: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ids_are_consistent(self) -> "Scene":
        action_ids = [a.id for a in self.actions]
        segment_ids = [s.id for s in self.narration_segments]
        all_ids = action_ids + segment_ids
        duplicates = {i for i in all_ids if all_ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"duplicate ids in scene {self.id}: {sorted(duplicates)}")

        beat_ids = {b.id for b in self.beats}
        orphans = [s.id for s in self.narration_segments if s.beat_id and s.beat_id not in beat_ids]
        if orphans:
            raise ValueError(f"segments {orphans} reference beats that are not in scene {self.id}")

        known = set(all_ids)
        for segment in self.narration_segments:
            unknown = [a for a in segment.linked_actions if a not in action_ids]
            if unknown:
                raise ValueError(f"segment {segment.id} links unknown actions: {unknown}")
        for action in self.actions:
            if action.start_after is not None and action.start_after not in known:
                raise ValueError(f"action {action.id} starts after unknown id {action.start_after!r}")
        return self


class TutorialScenes(StrictModel):
    """scenes.json"""

    title: str
    scenes: list[Scene]

    @property
    def estimated_duration_seconds(self) -> float:
        return sum(s.estimated_duration_seconds for s in self.scenes)
