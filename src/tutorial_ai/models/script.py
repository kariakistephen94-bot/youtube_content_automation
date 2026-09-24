"""Teaching script models.

A script is a sequence of *beats*. Each beat is one thing the instructor says plus (optionally)
one thing they do on screen. Code beats carry the complete file content after the beat; the
typing engine later diffs that against what is on disk and types only the difference. This keeps
the script deterministic and lets any point in the tutorial be reconstructed by replaying beats.
"""

import re
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from tutorial_ai.models.base import StrictModel, validate_workspace_path
from tutorial_ai.models.tutorial import TutorialPlan

# Phrases that make narration sound machine-written. Checked case-insensitively; a match fails
# validation, so the AI is automatically asked to rewrite the line.
BANNED_PHRASES = (
    "delve", "ever-evolving", "ever evolving", "unlock the power", "unleash", "revolutioniz",
    "game-changing", "game changer", "game-changer", "harness the power", "tapestry",
    "embark on", "in today's fast-paced", "in the realm of", "without further ado",
)  # fmt: skip
_BANNED = re.compile("|".join(re.escape(p) for p in BANNED_PHRASES), re.IGNORECASE)


class BeatKind(StrEnum):
    TALK = "talk"  # narration only; the screen stays as it is
    WRITE_CODE = "write_code"  # create or edit `file`; `code_after` is the full new content
    DELETE_FILE = "delete_file"
    RUN_COMMAND = "run_command"
    STOP_PROCESS = "stop_process"  # Ctrl+C the running background process (e.g. dev server)
    HIGHLIGHT = "highlight"  # point at `highlight` text inside `file` while explaining it
    OPEN_BROWSER = "open_browser"
    TRY_ENDPOINT = "try_endpoint"  # in the API docs (Swagger UI): try `method` `path` and show the response


HTTP_METHODS = ("get", "post", "put", "patch", "delete")


class SampleFile(StrictModel):
    """A small file created for a demo request (e.g. the file uploaded to an upload endpoint)."""

    name: str = Field(min_length=1, description="File name only, e.g. hello.txt (no folders).")
    content: str = Field(default="", description="Text content of the file.")

    @field_validator("name")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        value = value.strip()
        if "/" in value or "\\" in value or value in (".", "..") or not value:
            raise ValueError(f"sample file name must be a plain file name, got {value!r}")
        return value


class RequestField(StrictModel):
    """One parameter or form-field value of a demo request."""

    name: str = Field(min_length=1, description="The parameter/field name as shown in the docs.")
    value: str = Field(description="The value to enter. \"@hello.txt\" uploads the sample file hello.txt.")


class BeatDraft(StrictModel):
    """One beat as written by the AI (no id yet)."""

    kind: BeatKind
    narration: str = Field(
        default="",
        description="What the instructor says during this beat, in natural spoken English. "
        "1-4 sentences. Empty only if nothing needs to be said.",
    )
    file: str | None = Field(
        default=None, description="write_code/delete_file/highlight: workspace-relative path."
    )
    code_after: str | None = Field(
        default=None,
        description="write_code only: the COMPLETE content of `file` after this beat, not a snippet.",
    )
    command: str | None = Field(default=None, description="run_command only: the exact command.")
    background: bool = Field(
        default=False,
        description="run_command only: true for long-running processes such as dev servers.",
    )
    expect_output: str | None = Field(
        default=None,
        description="run_command only: a short substring the command prints on success.",
    )
    highlight: str | None = Field(
        default=None,
        description="highlight only: exact text copied from the current content of `file`.",
    )
    url: str | None = Field(
        default=None,
        description="open_browser: the http(s) URL to show. try_endpoint: the URL of the API docs "
        "page (Swagger UI), e.g. http://127.0.0.1:8000/docs.",
    )
    method: str | None = Field(
        default=None, description="try_endpoint only: HTTP method - get, post, put, patch or delete."
    )
    path: str | None = Field(
        default=None,
        description="try_endpoint only: the route path exactly as the docs show it, e.g. /upload.",
    )
    params: list[RequestField] | None = Field(
        default=None,
        description="try_endpoint only: values for the endpoint's parameters and form fields. For "
        "a file field use \"@name\" to upload the sample file called name (declared in "
        "sample_files), e.g. [{\"name\": \"file\", \"value\": \"@hello.txt\"}].",
    )
    body: str | None = Field(
        default=None, description="try_endpoint only: the JSON request body, as text, if the endpoint takes one."
    )
    sample_files: list[SampleFile] | None = Field(
        default=None,
        description="try_endpoint only: small text files to upload in the demo, referenced from params as @name.",
    )

    @field_validator("narration")
    @classmethod
    def _natural_narration(cls, value: str) -> str:
        match = _BANNED.search(value)
        if match:
            raise ValueError(
                f"narration uses the phrase {match.group(0)!r}, which sounds machine-written. "
                "Rephrase it the way a programming YouTuber would say it."
            )
        return value.strip()

    @field_validator("file")
    @classmethod
    def _workspace_path(cls, value: str | None) -> str | None:
        return None if value is None else validate_workspace_path(value)

    @model_validator(mode="after")
    def _fields_match_kind(self) -> "BeatDraft":
        required = {
            BeatKind.WRITE_CODE: ("file", "code_after"),
            BeatKind.DELETE_FILE: ("file",),
            BeatKind.RUN_COMMAND: ("command",),
            BeatKind.HIGHLIGHT: ("file", "highlight"),
            BeatKind.OPEN_BROWSER: ("url",),
            BeatKind.TRY_ENDPOINT: ("url", "method", "path"),
        }.get(self.kind, ())
        missing = [name for name in required
                   if (getattr(self, name) is None if name == "code_after" else not getattr(self, name))]
        if missing:
            raise ValueError(f"{self.kind.value} beat requires {', '.join(missing)}")
        if self.kind in (BeatKind.TALK, BeatKind.STOP_PROCESS) and not self.narration:
            raise ValueError(f"{self.kind.value} beat needs narration")
        if self.url is not None and not re.match(r"^https?://", self.url):
            raise ValueError(f"url must start with http:// or https://, got {self.url!r}")
        if self.kind is BeatKind.TRY_ENDPOINT:
            self._check_request()
        return self

    def _check_request(self) -> None:
        self.method = (self.method or "").strip().lower()
        if self.method not in HTTP_METHODS:
            raise ValueError(f"try_endpoint method must be one of {', '.join(HTTP_METHODS)}, got {self.method!r}")
        self.path = (self.path or "").strip()
        if not self.path.startswith("/") or any(c.isspace() for c in self.path):
            raise ValueError(f"try_endpoint path must be a route path such as /upload, got {self.path!r}")
        if self.body is not None:
            import json

            try:
                json.loads(self.body)
            except ValueError as exc:
                raise ValueError(f"try_endpoint body must be valid JSON text: {exc}") from None
        declared = {sample.name for sample in self.sample_files or []}
        for field in self.params or []:
            if field.value.startswith("@") and field.value[1:] not in declared:
                raise ValueError(
                    f"params {field.name!r} refers to sample file {field.value[1:]!r}, which is not "
                    "declared in sample_files (add {\"name\": ..., \"content\": ...} there)"
                )

    @property
    def param_values(self) -> dict[str, str]:
        """try_endpoint: field name -> value."""
        return {field.name: field.value for field in self.params or []}

    @property
    def sample_file_contents(self) -> dict[str, str]:
        """try_endpoint: sample file name -> content."""
        return {sample.name: sample.content for sample in self.sample_files or []}


class ScriptSectionDraft(StrictModel):
    """The AI's output for one tutorial section."""

    beats: list[BeatDraft] = Field(min_length=1)
    state_summary: str = Field(
        description="2-4 sentences describing the project at the end of this section: files, "
        "what works, what is still missing. Used as context for the next sections."
    )

    @model_validator(mode="after")
    def _opens_with_narration(self) -> "ScriptSectionDraft":
        if not self.beats[0].narration:
            raise ValueError("the first beat of a section must have narration introducing it")
        return self


class ScriptBeat(BeatDraft):
    id: str


class ScriptSection(StrictModel):
    section_number: int = Field(ge=1)
    title: str
    beats: list[ScriptBeat] = Field(min_length=1)
    state_summary: str


class FileSnapshot(StrictModel):
    path: str
    content: str


class ConvergenceStatus(StrEnum):
    IDENTICAL = "identical"
    SIMILAR = "similar"  # same file, minor differences
    DIFFERENT = "different"
    NOT_BUILT = "not_built"  # in the repository, never created in the tutorial
    TUTORIAL_ONLY = "tutorial_only"  # created in the tutorial, not in the repository


class ConvergenceItem(StrictModel):
    path: str
    status: ConvergenceStatus
    similarity: float = Field(ge=0, le=1)


class TutorialScript(StrictModel):
    title: str
    sections: list[ScriptSection] = Field(default_factory=list)
    convergence: list[ConvergenceItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def beats(self) -> list[ScriptBeat]:
        return [beat for section in self.sections for beat in section.beats]


class ScriptSectionRequest(StrictModel):
    """Everything the AI needs to write one section."""

    plan: TutorialPlan
    section_number: int
    repository_summary: str
    previous_state_summaries: list[str] = Field(default_factory=list)
    workspace_files: list[FileSnapshot] = Field(default_factory=list)
    running_processes: list[str] = Field(default_factory=list)
    reference_files: list[FileSnapshot] = Field(default_factory=list)
    feedback: list[str] = Field(
        default_factory=list, description="Problems with a previous attempt that must be fixed."
    )
