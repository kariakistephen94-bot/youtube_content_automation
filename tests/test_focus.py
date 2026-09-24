"""Focus checks: keystrokes must never go to a window that came to the front mid-typing."""

import subprocess
import sys
from types import SimpleNamespace

import pytest

from tutorial_ai.automation import macos
from tutorial_ai.automation.macos import AutomationError, Keyboard
from tutorial_ai.automation.typing import HumanTypingEngine
from tutorial_ai.automation.vscode import VSCodeController

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS focus helpers")


def test_front_application_asks_launch_services_not_the_cached_workspace(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args == ["lsappinfo", "front"]:
            return SimpleNamespace(stdout="ASN:0x0-0x23023:\n")
        return SimpleNamespace(stdout='"pid"=482\n"LSDisplayName"="Google Chrome"\n')

    monkeypatch.setattr(macos.subprocess, "run", fake_run)
    assert macos.front_application() == (482, "Google Chrome")
    assert calls[1][-1] == "ASN:0x0-0x23023:"


def test_front_application_falls_back_when_lsappinfo_is_unavailable(monkeypatch):
    def broken(*args, **kwargs):
        raise subprocess.TimeoutExpired("lsappinfo", 5)

    monkeypatch.setattr(macos.subprocess, "run", broken)
    app = SimpleNamespace(processIdentifier=lambda: 7, localizedName=lambda: "Code")
    workspace = SimpleNamespace(frontmostApplication=lambda: app)
    monkeypatch.setattr(macos, "NSWorkspace", SimpleNamespace(sharedWorkspace=lambda: workspace))
    assert macos.front_application() == (7, "Code")


def _keyboard(front: list, typed: list) -> Keyboard:
    keyboard = object.__new__(Keyboard)  # skips the Accessibility permission check
    keyboard.expected_app, keyboard.expected_pid = "Code", 100
    keyboard.type_char = typed.append
    return keyboard


def test_typing_stops_at_the_first_keystroke_after_another_window_comes_forward(monkeypatch):
    fronts = iter([(100, "Code")] * 3 + [(482, "Google Chrome")] * 10)
    monkeypatch.setattr(macos, "front_application", lambda: next(fronts))
    monkeypatch.setattr("tutorial_ai.automation.vscode.time.sleep", lambda s: None)
    typed: list[str] = []
    controller = object.__new__(VSCodeController)
    controller.typing = HumanTypingEngine()
    controller._keyboard = _keyboard([], typed)

    with pytest.raises(AutomationError, match="Google Chrome"):
        controller.type_text('uv add "fastapi[standard]"', "terminal")
    assert "".join(typed) == "uv "  # nothing was sent while Chrome was in front


def test_another_vscode_window_counts_as_lost_focus(monkeypatch):
    monkeypatch.setattr(macos, "front_application", lambda: (999, "Code"))
    with pytest.raises(AutomationError, match="another Code window"):
        _keyboard([], []).ensure_focus()
