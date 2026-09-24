"""The recording sandbox: host-side docker commands, and the X11 input layer used inside it."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from tutorial_ai import sandbox
from tutorial_ai.automation import x11
from tutorial_ai.automation.macos import AutomationError
from tutorial_ai.recording.recorder import capture_command, screen_captures


def test_run_command_mounts_source_read_only_and_output_read_write(tmp_path):
    command = sandbox.run_command("/usr/local/bin/docker", ["record", "demo"], tmp_path)
    joined = " ".join(command)
    assert command[:3] == ["/usr/local/bin/docker", "run", "--rm"]
    assert f"{sandbox.REPO_ROOT / 'src'}:/opt/tutorial-ai/src:ro" in command
    assert f"{tmp_path.resolve()}:/opt/tutorial-ai/output" in command
    assert "127.0.0.1:6080:6080" in joined  # live view only on localhost
    assert command[-3:] == [sandbox.IMAGE, "record", "demo"]


def test_record_args_pass_scene_selection_through():
    assert sandbox.record_args("demo", ["scene_002", "scene_004"], None, "fast") == [
        "record", "demo", "--speed", "fast", "--scene", "scene_002", "--scene", "scene_004"]
    assert sandbox.record_args("demo", None, "scene_003", "normal") == [
        "record", "demo", "--speed", "normal", "--from-scene", "scene_003"]


def test_status_without_docker_explains_what_to_install(monkeypatch):
    monkeypatch.setattr(sandbox, "find_docker", lambda: None)
    state = sandbox.status()
    assert not state.daemon_running and "OrbStack" in state.message
    with pytest.raises(sandbox.SandboxError, match="container runtime"):
        sandbox.require_ready()


def test_status_with_docker_but_no_image(monkeypatch):
    monkeypatch.setattr(sandbox, "find_docker", lambda: "docker")

    def fake(docker, *args, timeout=15):
        ok = args[0] == "info"  # daemon up, image missing, nothing running
        return SimpleNamespace(returncode=0 if ok or args[0] == "ps" else 1, stdout="")

    monkeypatch.setattr(sandbox, "_docker", fake)
    state = sandbox.status()
    assert state.daemon_running and not state.image_built and not state.running
    with pytest.raises(sandbox.SandboxError, match="not built"):
        sandbox.require_ready()


def test_x11_capture_command_grabs_the_virtual_display(tmp_path):
    command = capture_command("ffmpeg", ":99", tmp_path / "a.mp4", 1920, 1080, 30)
    assert command[command.index("-f") + 1] == "x11grab"
    assert command[command.index("-i") + 1] == ":99+0,0"
    assert command[command.index("-video_size") + 1] == "1920x1080"


def test_x11_captures_count_as_screen_captures():
    table = {10: (1, "python record"), 11: (10, "/usr/bin/ffmpeg -f x11grab -i :99+0,0 out.mp4"),
             12: (999, "ffmpeg -f x11grab -i :99+0,0 old.mp4")}
    stale, live = screen_captures(table)
    assert stale == [12] and [pid for pid, _, _ in live] == [11]


@pytest.mark.parametrize(("key", "mods", "expected"), [
    ("s", ["cmd"], "ctrl+s"),
    ("p", ["cmd", "shift"], "ctrl+shift+p"),
    ("`", ["ctrl", "shift"], "ctrl+shift+grave"),
    ("up", ["cmd"], "ctrl+Home"),
    ("down", ["cmd"], "ctrl+End"),
    ("right", ["shift"], "shift+Right"),
    ("return", None, "Return"),
    ("c", ["ctrl"], "ctrl+c"),
    ("1", ["cmd"], "ctrl+1"),
])
def test_mac_shortcuts_become_linux_vscode_shortcuts(key, mods, expected):
    assert x11.key_combo(key, mods) == expected


def test_unknown_keys_are_refused():
    with pytest.raises(AutomationError, match="Unknown key"):
        x11.key_combo("f13")


def test_x11_keyboard_types_through_xdotool_and_checks_focus(monkeypatch):
    calls = []
    monkeypatch.setattr(x11, "_xdotool", lambda *args, check=True: calls.append(args) or "")
    monkeypatch.setattr(x11, "front_application", lambda: (100, "Code"))
    keyboard = object.__new__(x11.Keyboard)
    keyboard.expected_app, keyboard.expected_pid = "Code", 100
    keyboard.type_char("(")
    keyboard.type_char("\n")
    keyboard.shortcut("cmd+s")
    assert calls == [("type", "--clearmodifiers", "--delay", "0", "--", "("),
                     ("key", "--clearmodifiers", "Return"),
                     ("key", "--clearmodifiers", "ctrl+s")]
    monkeypatch.setattr(x11, "front_application", lambda: (555, "Google Chrome"))
    with pytest.raises(AutomationError, match="Google Chrome"):
        keyboard.shortcut("cmd+s")


def test_app_names_match_what_callers_compare_against(tmp_path, monkeypatch):
    real_path = Path

    def fake_path(value):
        return tmp_path / "comm" if str(value).startswith("/proc/") else real_path(value)

    (tmp_path / "comm").write_text("chrome\n")
    monkeypatch.setattr(x11, "Path", fake_path)
    assert x11.app_name(1234) == "Google Chrome"
    (tmp_path / "comm").write_text("code\n")
    assert x11.app_name(1234) == "Code"
