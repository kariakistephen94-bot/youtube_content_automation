"""Tutorial request and plan models.

`TutorialPlan` is produced by the AI, so its field descriptions are part of the prompt.
"""

from pydantic import Field, model_validator

from tutorial_ai.models.base import Difficulty, StrictModel


class TutorialRequest(StrictModel):
    """What the user asked for on the command line."""

    title: str | None = None
    audience: Difficulty = Difficulty.BEGINNER
    target_duration_minutes: float = Field(default=20, gt=0, le=180)
    notes: str | None = Field(default=None, description="Extra guidance from the user.")


class TutorialSection(StrictModel):
    section_number: int = Field(ge=1)
    title: str = Field(description="Short, viewer-facing title, e.g. 'Build the upload endpoint'.")
    goal: str = Field(description="What the viewer can do or understands at the end of the section.")
    concepts: list[str] = Field(default_factory=list, description="Concepts explained here.")
    files_involved: list[str] = Field(
        default_factory=list,
        description="Repository-relative files created or changed in this section.",
    )
    commands: list[str] = Field(
        default_factory=list, description="Exact commands run on screen in this section."
    )
    expected_result: str = Field(
        description="What is visibly working at the end, e.g. 'POST /uploads saves a PDF'."
    )
    estimated_duration_seconds: float = Field(gt=0)


class TutorialPlan(StrictModel):
    title: str
    audience: Difficulty
    difficulty: Difficulty
    summary: str = Field(description="2-3 sentences: what the viewer builds and learns.")
    learning_objectives: list[str] = Field(min_length=1)
    prerequisites: list[str] = Field(default_factory=list)
    simplifications: list[str] = Field(
        default_factory=list,
        description="Where the tutorial deliberately differs from the repository to keep it "
        "teachable (e.g. 'Docker and CI are not covered'), and why.",
    )
    final_architecture: str = Field(
        description="The project structure the viewer ends up with, and how it maps to the repository."
    )
    estimated_duration_seconds: float = Field(gt=0)
    sections: list[TutorialSection] = Field(min_length=1)

    @model_validator(mode="after")
    def _sections_are_numbered_in_order(self) -> "TutorialPlan":
        numbers = [s.section_number for s in self.sections]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError(f"sections must be numbered 1..N in order, got {numbers}")
        return self

    def section(self, number: int) -> TutorialSection:
        return self.sections[number - 1]
