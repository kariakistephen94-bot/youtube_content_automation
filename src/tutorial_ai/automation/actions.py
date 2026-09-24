"""ActionCompiler: script beats -> the strict action language, deterministically (no AI).

The important part is `write_code`: the beat carries the whole file after the edit, and the
compiler turns that into line-level edits (insert / delete) against the file as it currently is,
so on screen only the changed lines are typed. Every action gets an expected duration from the
typing engine, which the timeline builder uses.
"""

import difflib

from pydantic import BaseModel, Field

from tutorial_ai.automation.typing import HumanTypingEngine
from tutorial_ai.models.action import Action, ActionType
from tutorial_ai.models.scene import Scene
from tutorial_ai.models.script import BeatKind, ScriptBeat
from tutorial_ai.tutorial.workspace_state import WorkspaceState

# Seconds of on-screen time for things the typing engine cannot estimate.
OVERHEAD = {
    "open_file": 1.0,
    "create_file": 1.5,
    "save": 0.6,
    "highlight": 2.5,  # find + leave the selection visible for a moment
    "terminal": 1.5,
    "command_short": 2.0,
    "command_install": 15.0,
    "command_tests": 8.0,
    "command_background": 5.0,
    "stop_process": 1.5,
    "browser": 6.0,
    "try_endpoint": 14.0,  # expand, Try it out, Execute, read the response
    "try_endpoint_field": 2.0,  # per field filled in
    "delete_file": 1.5,
    "cursor": 0.4,
}
_INSTALL_WORDS = ("add", "sync", "install", "init", "create", "npx", "pip")
_TEST_WORDS = ("pytest", "test", "jest", "vitest")


def normalize(content: str) -> str:
    """Files always end with exactly one newline: VS Code then shows an empty last line, so
    appending is simply 'go to end of file and type'."""
    return content.rstrip("\n") + "\n" if content.strip() else ""


class LineEdit(BaseModel):
    """One edit in the *current* file's line numbering (1-based, applied in order)."""

    kind: str = Field(pattern="^(insert|delete)$")
    line: int = Field(ge=1)
    count: int = Field(default=0, ge=0, description="delete: number of lines removed")
    text: str = Field(default="", description="insert: text typed, ending with a newline")


def plan_edits(before: str, after: str) -> list[LineEdit]:
    """Line-level edits that turn `before` into `after`, in top-to-bottom order, each expressed in
    the line numbers valid at the moment it is applied."""
    old = normalize(before).splitlines(keepends=True)
    new = normalize(after).splitlines(keepends=True)
    edits: list[LineEdit] = []
    offset = 0  # how many lines earlier edits added (+) or removed (-)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        line = i1 + 1 + offset
        if tag in ("delete", "replace"):
            edits.append(LineEdit(kind="delete", line=line, count=i2 - i1))
            offset -= i2 - i1
        if tag in ("insert", "replace"):
            text = "".join(new[j1:j2])
            edits.append(LineEdit(kind="insert", line=line, text=text))
            offset += j2 - j1
    return edits


class CompiledScene(BaseModel):
    scene_id: str
    actions: list[Action]
    typed_characters: int


class ActionCompiler:
    def __init__(self, typing: HumanTypingEngine | None = None) -> None:
        self._typing = typing or HumanTypingEngine()

    def compile_scene(self, scene: Scene, state: WorkspaceState) -> CompiledScene:
        """Compile a scene's beats. `state` is the workspace as it is when the scene starts and is
        advanced beat by beat (files use normalized content)."""
        actions: list[Action] = []
        typed = 0
        terminal_open = bool(state.running)
        current_file: str | None = None
        counter = 0

        def add(beat: ScriptBeat, type_: ActionType, params: dict, seconds: float, wait: float = 0.0) -> Action:
            nonlocal counter
            counter += 1
            action = Action(
                id=f"{beat.id}_a{counter:02d}", beat_id=beat.id, type=type_, parameters=params,
                start_after=actions[-1].id if actions else None,
                expected_duration_seconds=round(seconds, 2), wait_after_seconds=wait,
            )
            actions.append(action)
            return action

        for beat in scene.beats:
            match beat.kind:
                case BeatKind.WRITE_CODE:
                    path, after = beat.file, normalize(beat.code_after or "")
                    before = state.files.get(path)
                    if before is None:
                        add(beat, ActionType.CREATE_FILE, {"path": path}, OVERHEAD["create_file"])
                        if after:  # an empty file (e.g. __init__.py) needs no typing
                            add(beat, ActionType.TYPE_CODE, {"text": after}, self._typing.duration(after))
                            typed += len(after)
                    else:
                        if current_file != path:
                            add(beat, ActionType.OPEN_FILE, {"path": path}, OVERHEAD["open_file"])
                        for edit in plan_edits(before, after):
                            typed += self._emit_edit(beat, edit, before, add)
                    add(beat, ActionType.SAVE_FILE, {}, OVERHEAD["save"], wait=0.3)
                    current_file = path
                    state.files[path] = after
                case BeatKind.HIGHLIGHT:
                    if current_file != beat.file:
                        add(beat, ActionType.OPEN_FILE, {"path": beat.file}, OVERHEAD["open_file"])
                        current_file = beat.file
                    params = {"text": beat.highlight}
                    position = find_position(state.files.get(beat.file or "", ""), beat.highlight or "")
                    if position:
                        params["line"], params["column"] = position
                    add(beat, ActionType.HIGHLIGHT_TEXT, params, OVERHEAD["highlight"])
                case BeatKind.RUN_COMMAND:
                    if not terminal_open:
                        add(beat, ActionType.OPEN_TERMINAL, {}, OVERHEAD["terminal"])
                        terminal_open = True
                    else:
                        add(beat, ActionType.FOCUS_TERMINAL, {}, 0.5)
                    params = {"command": beat.command, "background": beat.background}
                    if beat.expect_output:
                        params["expect_output"] = beat.expect_output
                    if beat.background:
                        params["wait_url"] = _url_for(beat, scene)
                    seconds = self._typing.duration(beat.command or "", "terminal") + _command_seconds(beat)
                    add(beat, ActionType.RUN_COMMAND, params, seconds, wait=0.5)
                    typed += len(beat.command or "")
                    if beat.background:
                        state.running.append(beat.command or "")
                case BeatKind.STOP_PROCESS:
                    add(beat, ActionType.FOCUS_TERMINAL, {}, 0.5)
                    add(beat, ActionType.KEYBOARD_SHORTCUT, {"keys": ["ctrl", "c"]}, OVERHEAD["stop_process"])
                    if state.running:
                        state.running.pop()
                case BeatKind.OPEN_BROWSER:
                    add(beat, ActionType.OPEN_BROWSER, {"url": beat.url}, OVERHEAD["browser"])
                case BeatKind.TRY_ENDPOINT:
                    params = {
                        "url": beat.url, "method": beat.method, "path": beat.path,
                        "params": beat.param_values, "body": beat.body,
                        "sample_files": beat.sample_file_contents,
                    }
                    fields = len(beat.param_values) + (1 if beat.body else 0)
                    seconds = OVERHEAD["try_endpoint"] + OVERHEAD["try_endpoint_field"] * fields
                    add(beat, ActionType.TRY_ENDPOINT, params, seconds, wait=0.5)
                case BeatKind.DELETE_FILE:
                    add(beat, ActionType.DELETE_FILE, {"path": beat.file}, OVERHEAD["delete_file"])
                    state.files.pop(beat.file, None)
                    if current_file == beat.file:
                        current_file = None
                case BeatKind.TALK:
                    pass
        return CompiledScene(scene_id=scene.id, actions=actions, typed_characters=typed)

    def _emit_edit(self, beat: ScriptBeat, edit: LineEdit, before: str, add) -> int:
        """Emit the actions for one line edit; returns characters typed."""
        line_count = len(normalize(before).splitlines())
        if edit.kind == "delete":
            add(beat, ActionType.MOVE_CURSOR, {"line": edit.line, "column": 1}, OVERHEAD["cursor"])
            add(beat, ActionType.KEYBOARD_SHORTCUT, {"keys": ["shift", "down"], "presses": edit.count}, 0.3)
            add(beat, ActionType.PRESS_KEY, {"key": "backspace"}, 0.4, wait=0.3)
            return 0
        if edit.line > line_count:  # appending after the last line
            add(beat, ActionType.KEYBOARD_SHORTCUT, {"keys": ["cmd", "end"]}, OVERHEAD["cursor"])
        else:
            add(beat, ActionType.MOVE_CURSOR, {"line": edit.line, "column": 1}, OVERHEAD["cursor"])
        if not edit.text:
            return 0
        add(beat, ActionType.TYPE_CODE, {"text": edit.text}, self._typing.duration(edit.text))
        return len(edit.text)


def find_position(content: str, text: str) -> tuple[int, int] | None:
    """(line, column), 1-based, of the first line of `text` in `content`."""
    first_line = text.splitlines()[0] if text else ""
    if not first_line:
        return None
    for number, line in enumerate(normalize(content).splitlines(), 1):
        column = line.find(first_line)
        if column >= 0:
            return number, column + 1
    return None


def _command_seconds(beat: ScriptBeat) -> float:
    command = (beat.command or "").lower()
    if beat.background:
        return OVERHEAD["command_background"]
    if any(word in command for word in _TEST_WORDS):
        return OVERHEAD["command_tests"]
    if any(word in command.split() for word in _INSTALL_WORDS) or "install" in command:
        return OVERHEAD["command_install"]
    return OVERHEAD["command_short"]


def _url_for(beat: ScriptBeat, scene: Scene) -> str | None:
    """A URL that proves a background server is up: the next browser beat in the scene."""
    seen = False
    for other in scene.beats:
        if other.id == beat.id:
            seen = True
        elif seen and other.kind in (BeatKind.OPEN_BROWSER, BeatKind.TRY_ENDPOINT) and other.url:
            return other.url
    return None
