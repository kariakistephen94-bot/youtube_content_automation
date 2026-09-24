"""Repository analysis models.

`RepositoryInsights` is exactly what the AI provider is asked to produce; its JSON schema (with the
field descriptions below) is sent to the model. `RepositoryAnalysis` extends it with facts that are
computed deterministically by the scanner (file tree, language stats, ignored files) so the model is
never trusted for things we can measure ourselves.
"""

from datetime import datetime
from typing import Literal

from pydantic import Field

from tutorial_ai.models.base import Difficulty, StrictModel


class Dependency(StrictModel):
    name: str = Field(description="Package name exactly as declared in the manifest.")
    version: str | None = Field(default=None, description="Version constraint if declared.")
    purpose: str = Field(description="One sentence: what this project uses the dependency for.")
    dev: bool = Field(default=False, description="True if only needed for development/testing.")


class EntryPoint(StrictModel):
    path: str = Field(description="Repository-relative path of the file.")
    description: str = Field(description="How the application is started from this file.")


class ImportantFile(StrictModel):
    path: str = Field(description="Repository-relative path of the file.")
    role: str = Field(description="What this file does in the application.")
    teaching_notes: str = Field(
        description="What a learner needs to understand about this file, and when to introduce it."
    )


class Concept(StrictModel):
    name: str = Field(description="Short name, e.g. 'UploadFile', 'dependency injection'.")
    explanation: str = Field(description="Plain-language explanation suitable for the audience.")
    related_files: list[str] = Field(
        default_factory=list, description="Repository-relative paths where the concept appears."
    )


class CommandSpec(StrictModel):
    command: str = Field(description="Exact shell command.")
    description: str = Field(description="What the command does.")
    working_directory: str = Field(
        default=".", description="Repository-relative directory to run the command from."
    )


class EnvironmentVariable(StrictModel):
    name: str
    description: str
    required: bool = Field(description="True if the app fails to start/work without it.")
    example: str | None = Field(
        default=None, description="A safe placeholder example value. Never a real secret."
    )


class ExternalService(StrictModel):
    name: str = Field(description="e.g. 'PostgreSQL', 'Stripe API', 'S3'.")
    purpose: str
    required_to_run: bool = Field(
        description="True if the app cannot be demonstrated locally without this service."
    )


class TeachingStep(StrictModel):
    title: str = Field(description="Short step title, e.g. 'Build the upload endpoint'.")
    goal: str = Field(description="What the learner can do or understands after this step.")
    concepts: list[str] = Field(default_factory=list)
    files: list[str] = Field(
        default_factory=list, description="Repository-relative files created or changed in this step."
    )


class RepositoryInsights(StrictModel):
    """The AI-produced understanding of a repository."""

    description: str = Field(description="2-4 sentences: what the project is and what it does.")
    primary_language: str
    frameworks: list[str] = Field(description="Frameworks and major libraries, e.g. 'FastAPI'.")
    dependencies: list[Dependency]
    entry_points: list[EntryPoint]
    important_files: list[ImportantFile] = Field(
        description="The files that matter for understanding and rebuilding the project, "
        "most important first. Only paths that appear in the provided file list."
    )
    important_concepts: list[Concept]
    application_flow: list[str] = Field(
        description="Ordered steps describing what happens at runtime, e.g. how a request flows."
    )
    setup_steps: list[str] = Field(
        description="Ordered steps to set up the project from a fresh clone."
    )
    run_commands: list[CommandSpec]
    test_commands: list[CommandSpec]
    environment_variables: list[EnvironmentVariable]
    external_services: list[ExternalService]
    difficulty: Difficulty = Field(description="Difficulty of rebuilding this project in a tutorial.")
    prerequisites: list[str] = Field(description="What a viewer should already know.")
    potential_teaching_order: list[TeachingStep] = Field(
        description="A teaching journey that rebuilds the project incrementally in the order that "
        "best builds understanding - NOT file order or commit order. Start from a minimal working "
        "version, run it early, then add features, refactors and structure step by step, converging "
        "on the real repository architecture."
    )
    teaching_notes: list[str] = Field(
        default_factory=list,
        description="Anything a tutorial author should know: parts that can be simplified or "
        "skipped for teaching, likely pitfalls, things that are hard to demonstrate on screen.",
    )


class RepositorySource(StrictModel):
    name: str
    kind: Literal["github", "local"]
    origin: str = Field(description="The GitHub URL or the local path given by the user.")
    local_path: str = Field(description="Where the repository was read from on disk.")
    commit: str | None = None
    branch: str | None = None


class LanguageShare(StrictModel):
    language: str
    files: int
    bytes: int
    percent: float


class ScanSummary(StrictModel):
    total_files: int
    ignored_files: int
    files_with_content_sent: int
    files_truncated: int
    files_omitted_for_budget: int
    estimated_context_tokens: int


class RepositoryAnalysis(RepositoryInsights):
    """Final analysis written to repository_analysis.json."""

    schema_version: int = 1
    repository_name: str
    source: RepositorySource
    analyzed_at: datetime
    ai_model: str
    languages: list[LanguageShare]
    project_structure: str
    files_to_ignore: list[str]
    scan_summary: ScanSummary
    validation_warnings: list[str] = Field(
        default_factory=list,
        description="Problems found when checking the AI output against the real repository.",
    )
