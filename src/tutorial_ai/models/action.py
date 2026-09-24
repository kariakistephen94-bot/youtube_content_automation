"""The strict action language.

The AI never controls the computer directly. It emits `Action`s; each action type has its own
parameter model, so an action with missing or unexpected parameters fails validation before
anything is executed. Shell commands are additionally checked by the command validator
(security module) before execution - schema validity alone does not make a command safe.
"""

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, SerializeAsAny, field_validator, model_validator

from tutorial_ai.models.base import StrictModel, validate_workspace_path


class ActionType(StrEnum):
    OPEN_VSCODE = "OPEN_VSCODE"
    OPEN_FOLDER = "OPEN_FOLDER"
    CREATE_FILE = "CREATE_FILE"
    CREATE_FOLDER = "CREATE_FOLDER"
    OPEN_FILE = "OPEN_FILE"
    DELETE_FILE = "DELETE_FILE"
    TYPE_TEXT = "TYPE_TEXT"
    TYPE_CODE = "TYPE_CODE"
    MOVE_CURSOR = "MOVE_CURSOR"
    SELECT_TEXT = "SELECT_TEXT"
    HIGHLIGHT_TEXT = "HIGHLIGHT_TEXT"
    DELETE_TEXT = "DELETE_TEXT"
    PRESS_KEY = "PRESS_KEY"
    KEYBOARD_SHORTCUT = "KEYBOARD_SHORTCUT"
    WAIT = "WAIT"
    OPEN_TERMINAL = "OPEN_TERMINAL"
    TYPE_TERMINAL = "TYPE_TERMINAL"
    RUN_COMMAND = "RUN_COMMAND"
    WAIT_FOR_COMMAND = "WAIT_FOR_COMMAND"
    OPEN_BROWSER = "OPEN_BROWSER"
    NAVIGATE_BROWSER = "NAVIGATE_BROWSER"
    CLICK_BROWSER = "CLICK_BROWSER"
    WAIT_FOR_BROWSER = "WAIT_FOR_BROWSER"
    TRY_ENDPOINT = "TRY_ENDPOINT"  # in Swagger UI: expand, Try it out, fill, Execute, show response
    SCROLL = "SCROLL"
    FOCUS_EDITOR = "FOCUS_EDITOR"
    FOCUS_TERMINAL = "FOCUS_TERMINAL"
    SAVE_FILE = "SAVE_FILE"
    PLAY_AUDIO = "PLAY_AUDIO"
    START_RECORDING = "START_RECORDING"
    STOP_RECORDING = "STOP_RECORDING"


class FailureBehavior(StrEnum):
    RETRY = "RETRY"
    REPAIR = "REPAIR"
    PAUSE = "PAUSE"  # V1 default: stop, log, let the developer fix and resume
    ABORT = "ABORT"


# --- Parameter models ---------------------------------------------------------------------------


class ActionParams(StrictModel):
    """Base for all parameter models. Used directly for actions that take no parameters."""


class WorkspacePathParams(ActionParams):
    path: str = Field(description="Path relative to the tutorial workspace root.")

    @field_validator("path")
    @classmethod
    def _must_stay_inside_workspace(cls, value: str) -> str:
        return validate_workspace_path(value)


class TextParams(ActionParams):
    text: str = Field(min_length=1)


class MoveCursorParams(ActionParams):
    line: int = Field(ge=1)
    column: int = Field(default=1, ge=1)


class TextMatchParams(ActionParams):
    """Targets the Nth occurrence of `text` in the current file. `line`/`column` (1-based) are
    filled in by the compiler when the file content is known, so no search box is needed."""

    text: str = Field(min_length=1)
    occurrence: int = Field(default=1, ge=1)
    line: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)


class PressKeyParams(ActionParams):
    key: str = Field(min_length=1)
    presses: int = Field(default=1, ge=1, le=50)


class ShortcutParams(ActionParams):
    keys: list[str] = Field(min_length=1, description="e.g. ['cmd', 's']")
    presses: int = Field(default=1, ge=1, le=500)


class WaitParams(ActionParams):
    seconds: float = Field(gt=0, le=600)


class RunCommandParams(ActionParams):
    command: str = Field(min_length=1)
    working_directory: str = "."
    background: bool = Field(
        default=False, description="True for long-running processes such as dev servers."
    )
    timeout_seconds: float = Field(default=120, gt=0)
    expect_output: str | None = Field(
        default=None, description="Substring that indicates success, e.g. 'Application startup'."
    )
    wait_url: str | None = Field(
        default=None, description="For background servers: poll this URL until it responds."
    )


class WaitForCommandParams(ActionParams):
    timeout_seconds: float = Field(default=60, gt=0)
    expect_output: str | None = None
    url: str | None = Field(default=None, description="Poll this URL until it responds.")


class UrlParams(ActionParams):
    url: str = Field(pattern=r"^https?://")


class OptionalUrlParams(ActionParams):
    url: str | None = Field(default=None, pattern=r"^https?://")


class SelectorParams(ActionParams):
    selector: str = Field(min_length=1)


class WaitForBrowserParams(ActionParams):
    selector: str | None = None
    timeout_seconds: float = Field(default=30, gt=0)


class TryEndpointParams(ActionParams):
    url: str = Field(pattern=r"^https?://", description="The API docs page (Swagger UI).")
    method: str = Field(pattern=r"^(get|post|put|patch|delete)$")
    path: str = Field(pattern=r"^/")
    params: dict[str, str] = Field(default_factory=dict, description="Field values by name; '@name' uploads a sample file.")
    body: str | None = None
    sample_files: dict[str, str] = Field(default_factory=dict, description="Sample file name -> content.")


class ScrollParams(ActionParams):
    target: Literal["editor", "terminal", "browser"] = "editor"
    direction: Literal["up", "down"] = "down"
    amount: int = Field(default=5, ge=1)


class PlayAudioParams(ActionParams):
    segment_id: str = Field(min_length=1)


PARAMS_BY_TYPE: dict[ActionType, type[ActionParams]] = {
    ActionType.OPEN_VSCODE: ActionParams,
    ActionType.OPEN_FOLDER: WorkspacePathParams,
    ActionType.CREATE_FILE: WorkspacePathParams,
    ActionType.CREATE_FOLDER: WorkspacePathParams,
    ActionType.OPEN_FILE: WorkspacePathParams,
    ActionType.DELETE_FILE: WorkspacePathParams,
    ActionType.TYPE_TEXT: TextParams,
    ActionType.TYPE_CODE: TextParams,
    ActionType.MOVE_CURSOR: MoveCursorParams,
    ActionType.SELECT_TEXT: TextMatchParams,
    ActionType.HIGHLIGHT_TEXT: TextMatchParams,
    ActionType.DELETE_TEXT: TextMatchParams,
    ActionType.PRESS_KEY: PressKeyParams,
    ActionType.KEYBOARD_SHORTCUT: ShortcutParams,
    ActionType.WAIT: WaitParams,
    ActionType.OPEN_TERMINAL: ActionParams,
    ActionType.TYPE_TERMINAL: TextParams,
    ActionType.RUN_COMMAND: RunCommandParams,
    ActionType.WAIT_FOR_COMMAND: WaitForCommandParams,
    ActionType.OPEN_BROWSER: OptionalUrlParams,
    ActionType.NAVIGATE_BROWSER: UrlParams,
    ActionType.CLICK_BROWSER: SelectorParams,
    ActionType.WAIT_FOR_BROWSER: WaitForBrowserParams,
    ActionType.TRY_ENDPOINT: TryEndpointParams,
    ActionType.SCROLL: ScrollParams,
    ActionType.FOCUS_EDITOR: ActionParams,
    ActionType.FOCUS_TERMINAL: ActionParams,
    ActionType.SAVE_FILE: ActionParams,
    ActionType.PLAY_AUDIO: PlayAudioParams,
    ActionType.START_RECORDING: ActionParams,
    ActionType.STOP_RECORDING: ActionParams,
}


class Action(StrictModel):
    id: str = Field(min_length=1)
    type: ActionType
    beat_id: str | None = Field(default=None, description="Script beat this action belongs to.")
    parameters: SerializeAsAny[ActionParams] = Field(default_factory=ActionParams)
    start_after: str | None = Field(
        default=None, description="Id of the action or narration segment this action waits for."
    )
    expected_duration_seconds: float | None = Field(default=None, ge=0)
    wait_after_seconds: float = Field(default=0.0, ge=0)
    failure_behavior: FailureBehavior = FailureBehavior.PAUSE

    @model_validator(mode="before")
    @classmethod
    def _parse_parameters_for_type(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "type" not in data:
            return data
        params_model = PARAMS_BY_TYPE[ActionType(data["type"])]
        params = data.get("parameters")
        if params is None:
            params = {}
        elif isinstance(params, ActionParams):
            params = params.model_dump()
        return {**data, "parameters": params_model.model_validate(params)}
