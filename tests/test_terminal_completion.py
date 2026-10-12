import subprocess
import time
from types import SimpleNamespace

import pytest

from tutorial_ai.automation import vscode as vscode_module
from tutorial_ai.automation.executor import ActionExecutor
from tutorial_ai.automation.macos import AutomationError
from tutorial_ai.automation.terminal import TerminalController
from tutorial_ai.automation.vscode import VSCodeController
from tutorial_ai.models.action import Action


def test_real_shell_reports_missing_command_and_preserves_cwd(tmp_path, monkeypatch):
    monkeypatch.setattr(vscode_module, "find_code_cli", lambda: "code")
    controller = VSCodeController(tmp_path, profile_root=tmp_path / "profile")
    controller.prepare_profile()
    rc = tmp_path / "profile" / "terminal.bashrc"
    shell = subprocess.Popen(["/bin/bash", "--noprofile", "--rcfile", str(rc), "-i"],
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             text=True, cwd=tmp_path)
    terminal = TerminalController(controller)
    try:
        assert terminal.wait_until_idle(5)
        for command, expected in [("tutorial_ai_missing_command", 127), ("mkdir child && cd child", 0),
                                  ("pwd > location.txt", 0), ("false | cat", 1)]:
            controller.terminal_status_path.unlink()
            shell.stdin.write(command + "\n")
            shell.stdin.flush()
            assert terminal.wait_until_idle(5)
            assert terminal.exit_code() == expected
        assert (tmp_path / "child" / "location.txt").read_text().strip() == str(tmp_path / "child")
    finally:
        shell.stdin.write("exit\n")
        shell.stdin.flush()
        shell.wait(timeout=5)
        shell.stdin.close()


def test_failure_stops_executor_before_next_action(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)
    status = tmp_path / "status"
    status.write_text("0\n")
    commands = []
    vscode = SimpleNamespace(terminal_status_path=status, open_terminal=lambda: None,
        type_text=lambda command, mode: commands.append(command),
        keyboard=SimpleNamespace(press=lambda _: status.write_text("127\n")))
    executor = ActionExecutor(vscode, TerminalController(vscode))
    with pytest.raises(AutomationError, match="npx.*exit code 127"):
        for command in ["npx create-next-app", "npm run dev"]:
            executor.execute(Action(id=command, type="RUN_COMMAND", parameters={"command": command}))
    assert commands == ["npx create-next-app"]


def test_missing_completion_is_not_success(tmp_path):
    terminal = TerminalController(SimpleNamespace(terminal_status_path=tmp_path / "absent"))
    assert not terminal.wait_until_idle(0)
    with pytest.raises(AutomationError, match="did not report"):
        terminal.require_success()


def test_preflight_names_missing_tool(monkeypatch):
    from tutorial_ai.automation import prerequisites
    monkeypatch.setattr(prerequisites.shutil, "which", lambda tool, **_: None if tool == "npx" else "/bin/tool")
    with pytest.raises(AutomationError, match="prerequisites: npx"):
        prerequisites.check_prerequisites(["npx create-next-app demo", "cd demo && npm install"])


def test_background_failure_is_reported_before_url_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)
    status = tmp_path / "status"
    status.write_text("0\n")
    vscode = SimpleNamespace(terminal_status_path=status, open_terminal=lambda: None,
        type_text=lambda *args: None,
        keyboard=SimpleNamespace(press=lambda _: status.write_text("1\n")))
    executor = ActionExecutor(vscode, TerminalController(vscode))
    with pytest.raises(AutomationError, match="npm run dev.*exit code 1"):
        executor.execute(Action(id="server", type="RUN_COMMAND", parameters={
            "command": "npm run dev", "background": True, "wait_url": "http://localhost:3000"}))


def test_terminal_focus_does_not_use_command_palette(tmp_path, monkeypatch):
    monkeypatch.setattr(vscode_module, "find_code_cli", lambda: "code")
    monkeypatch.setattr(time, "sleep", lambda _: None)
    controller = VSCodeController(tmp_path, profile_root=tmp_path / "profile")
    shortcuts = []
    controller._keyboard = SimpleNamespace(shortcut=shortcuts.append)
    controller.run_palette_command = lambda _: pytest.fail("Command palette must not open")
    controller.open_terminal()
    controller.open_terminal()
    assert shortcuts == ["ctrl+shift+`", "ctrl+alt+t"]
