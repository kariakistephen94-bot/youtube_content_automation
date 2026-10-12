"""ScenePlayer / ActionExecutor with a fake VS Code: verifies ordering, timing and recorded offsets."""

import time
from types import SimpleNamespace

from conftest import make_plan, make_sections

from tutorial_ai.automation.actions import ActionCompiler
from tutorial_ai.automation.executor import ActionExecutor
from tutorial_ai.automation.orchestrator import ScenePlayer
from tutorial_ai.models.script import ScriptBeat, ScriptSection, TutorialScript
from tutorial_ai.tutorial.scene_builder import build_scenes
from tutorial_ai.tutorial.workspace_state import WorkspaceState


class FakeVSCode:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.keyboard = SimpleNamespace(press=lambda key: self.calls.append(("press", key)),
                                        shortcut=lambda c: self.calls.append(("shortcut", c)))

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, *args))
        return method


class FakeTerminal:
    def __init__(self, busy_polls: int = 0) -> None:
        self.commands: list[str] = []
        self.idle_waits = 0
        self._busy_polls = busy_polls  # how many running_commands() calls report a running command

    def run_command(self, command: str, wait_seconds: float = 0.0, *, focus: bool = True) -> None:
        self.commands.append(command)

    def require_success(self) -> None:
        pass

    def exit_code(self):
        return None

    def running_commands(self) -> list[str]:
        if self._busy_polls > 0:
            self._busy_polls -= 1
            return ["uv"]
        return []

    def wait_until_idle(self, timeout: float, settle: float = 0.0) -> bool:
        self.idle_waits += 1
        while self.running_commands():
            pass
        return True


class FakeBrowser:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.current_url = None

    def open(self, url: str) -> bool:
        self.calls.append(("open", url))
        fresh = self.current_url != url
        self.current_url = url
        return fresh

    def try_endpoint(self, method, path, params, body, sample_files, refresh=False) -> str:
        self.calls.append(("try_endpoint", method, path, params, body, sample_files, refresh))
        return "200"

    def close(self) -> None:
        self.calls.append(("close",))


def _scene():
    sections = []
    for number, draft in make_sections().items():
        beats = [ScriptBeat(id=f"s{number:02d}_b{i:02d}", **b.model_dump()) for i, b in enumerate(draft.beats, 1)]
        sections.append(ScriptSection(section_number=number, title=make_plan().section(number).title,
                                      beats=beats, state_summary=draft.state_summary))
    scenes = build_scenes(TutorialScript(title="T", sections=sections), make_plan())
    state = WorkspaceState()
    for scene in scenes.scenes:
        scene.actions = ActionCompiler().compile_scene(scene, state).actions
        for segment in scene.narration_segments:
            segment.measured_duration_seconds = 0.05  # keep the test fast
    return scenes.scenes[2]  # append the endpoint, stop the server


def test_player_executes_actions_in_order_and_records_offsets(monkeypatch, tmp_path):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr("tutorial_ai.automation.orchestrator.time.sleep", lambda s: None)
    monkeypatch.setattr("tutorial_ai.automation.executor.time.sleep", lambda s: None)
    vscode, terminal = FakeVSCode(), FakeTerminal()
    player = ScenePlayer(ActionExecutor(vscode, terminal), tmp_path)

    beats, segments = player.play(_scene())

    names = [c[0] for c in vscode.calls]
    assert names[0] == "open_file"
    assert ("press_shortcut", "cmd+end") in vscode.calls
    assert any(c[0] == "type_text" and "@app.post" in c[1] for c in vscode.calls)
    assert ("save_file",) in vscode.calls
    assert ("press_shortcut", "ctrl+c") in vscode.calls  # stop_process
    assert [b.beat_id for b in beats] == ["s03_b01", "s03_b02"]
    assert beats[0].start_seconds <= beats[0].end_seconds <= beats[1].start_seconds
    assert [s.segment_id for s in segments] == ["s03_b01_n1", "s03_b02_n1"]
    assert segments[1].start_seconds >= beats[1].start_seconds


def test_foreground_commands_wait_for_the_terminal_to_finish(monkeypatch):
    """The next command is typed only when the previous one has really finished (process tree),
    not after a fixed budget - `uv add` on a slow network took longer than the 6s budget."""
    from tutorial_ai.models.action import Action

    monkeypatch.setattr("tutorial_ai.automation.executor.time.sleep", lambda s: None)
    vscode, terminal = FakeVSCode(), FakeTerminal(busy_polls=3)
    executor = ActionExecutor(vscode, terminal)
    executor.execute(Action(id="a1", type="RUN_COMMAND", parameters={"command": 'uv add "fastapi[standard]"'}))
    assert terminal.commands == ['uv add "fastapi[standard]"']
    assert terminal.idle_waits == 1 and terminal.running_commands() == []


def test_try_endpoint_goes_through_the_browser_controller(monkeypatch):
    from tutorial_ai.models.action import Action

    monkeypatch.setattr("tutorial_ai.automation.executor.time.sleep", lambda s: None)
    vscode, terminal, browser = FakeVSCode(), FakeTerminal(), FakeBrowser()
    executor = ActionExecutor(vscode, terminal, browser)
    executor.execute(Action(id="a1", type="OPEN_BROWSER", parameters={"url": "http://127.0.0.1:8000/docs"}))
    executor.execute(Action(id="a2", type="TRY_ENDPOINT", parameters={
        "url": "http://127.0.0.1:8000/docs", "method": "post", "path": "/upload",
        "params": {"file": "@hello.txt"}, "sample_files": {"hello.txt": "hi\n"},
    }))
    executor.execute(Action(id="a3", type="SAVE_FILE"))  # leaves the browser: VS Code comes back
    assert browser.calls[0] == ("open", "http://127.0.0.1:8000/docs")
    # the docs page was already open from OPEN_BROWSER: the demo reloads it to pick up the edits
    assert browser.calls[-1] == ("try_endpoint", "post", "/upload", {"file": "@hello.txt"}, None, {"hello.txt": "hi\n"}, True)
    assert ("activate",) in vscode.calls and vscode.calls.index(("activate",)) < vscode.calls.index(("save_file",))


def test_browser_actions_without_a_controller_pause_the_scene(monkeypatch):
    import pytest

    from tutorial_ai.automation.macos import AutomationError
    from tutorial_ai.models.action import Action

    monkeypatch.setattr("tutorial_ai.automation.executor.time.sleep", lambda s: None)
    executor = ActionExecutor(FakeVSCode(), FakeTerminal())
    with pytest.raises(AutomationError, match="browser automation is not available"):
        executor.execute(Action(id="a1", type="TRY_ENDPOINT", parameters={
            "url": "http://127.0.0.1:8000/docs", "method": "get", "path": "/"}))


def test_command_focuses_terminal_once_and_refocuses_after_editor(monkeypatch, tmp_path):
    from tutorial_ai.automation.terminal import TerminalController
    from tutorial_ai.models.action import Action

    monkeypatch.setattr(time, "sleep", lambda _: None)
    vscode = FakeVSCode()
    vscode.terminal_status_path = tmp_path / "status"
    terminal = TerminalController(vscode)
    monkeypatch.setattr(terminal, "require_success", lambda: None)
    monkeypatch.setattr(terminal, "wait_until_idle", lambda *args: True)
    executor = ActionExecutor(vscode, terminal)
    executor.execute(Action(id="focus", type="FOCUS_TERMINAL"))
    executor.execute(Action(id="run", type="RUN_COMMAND", parameters={"command": "python3 --version"}))
    assert sum(call[0] == "open_terminal" for call in vscode.calls) == 1
    executor.execute(Action(id="edit", type="OPEN_FILE", parameters={"path": "main.py"}))
    executor.execute(Action(id="run2", type="RUN_COMMAND", parameters={"command": "python3 --version"}))
    assert sum(call[0] == "open_terminal" for call in vscode.calls) == 2
