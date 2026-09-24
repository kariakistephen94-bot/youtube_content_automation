"""Guards that keep two recordings from fighting over the screen, and the terminal-idle detection
that stops the executor typing the next command while `uv add` is still running."""

import os
import signal

import pytest

from tutorial_ai.automation.terminal import running_in_terminal
from tutorial_ai.recording.recorder import RecorderError, RecordingLock, describe_exit, kill_stale_captures, screen_captures


def _table(rows):
    return {pid: (ppid, command) for pid, ppid, command in rows}


def test_live_captures_are_told_apart_from_stale_ones():
    table = _table([
        (100, 1, "/Users/x/.venv/bin/python tutorial-ai record demo"),
        (101, 100, "/usr/local/bin/ffmpeg -hide_banner -f avfoundation -framerate 30 -i 1:none out.mp4"),  # live: owner 100 runs
        (200, 1, "/usr/local/bin/ffmpeg -f avfoundation -i 1:none stale.mp4"),  # stale: reparented to launchd
        (201, 999, "ffmpeg -f avfoundation -i 1:none orphan.mp4"),  # stale: parent gone
        (300, 100, "ffmpeg -hide_banner -f avfoundation -list_devices true -i "),  # device listing, not a capture
        (301, 100, "/bin/zsh -c pgrep -f ffmpeg.*avfoundation"),  # a shell talking about ffmpeg
    ])
    stale, live = screen_captures(table)
    assert stale == [200, 201]
    assert live == [(101, 100, "/Users/x/.venv/bin/python tutorial-ai record demo")]


def test_kill_stale_captures_refuses_when_another_recording_owns_the_screen(monkeypatch):
    from tutorial_ai.recording import recorder

    killed = []
    monkeypatch.setattr(recorder, "screen_captures", lambda table=None: ([555], [(101, 100, "tutorial-ai record demo")]))
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(recorder.time, "sleep", lambda s: None)
    with pytest.raises(RecorderError, match="already being recorded.*pid 101.*started by pid 100"):
        kill_stale_captures()
    assert killed == [(555, signal.SIGKILL)]  # the stale one is still cleaned up first


def test_kill_stale_captures_returns_count_when_the_screen_is_free(monkeypatch):
    from tutorial_ai.recording import recorder

    monkeypatch.setattr(recorder, "screen_captures", lambda table=None: ([], []))
    assert kill_stale_captures() == 0


def test_describe_exit_explains_a_sigkill():
    assert describe_exit(1) == "exit code 1"
    assert describe_exit(-signal.SIGINT) == "killed by SIGINT"
    text = describe_exit(-signal.SIGKILL)
    assert text.startswith("killed by SIGKILL") and "another recording" in text


def test_recording_lock_is_exclusive_but_takes_over_from_a_dead_owner(tmp_path):
    path = tmp_path / "recording.lock"
    path.write_text(f"{os.getppid()}\n")  # held by a live process that is not us
    with pytest.raises(RecorderError, match="already running"):
        RecordingLock(path).acquire()
    assert path.read_text().strip() == str(os.getppid())  # left untouched

    path.unlink()
    first = RecordingLock(path)
    first.acquire()
    assert path.read_text().strip() == str(os.getpid())
    first.acquire()  # the same process may re-enter (record, then build)
    first.release()
    assert not path.exists()

    path.write_text("999999\n")  # a pid that (almost certainly) does not exist any more
    with RecordingLock(path):
        assert path.read_text().strip() == str(os.getpid())
    assert not path.exists()


def test_running_in_terminal_sees_the_command_below_the_integrated_shell():
    vscode, pty_host, shell = 10, 11, 12
    base = [
        (vscode, 1, "/Applications/Visual Studio Code.app/Contents/MacOS/Electron"),
        (pty_host, vscode, "/Applications/Visual Studio Code.app/Contents/Frameworks/Code Helper (Plugin)"),
        (shell, pty_host, "-zsh"),
        (20, 1, "/bin/zsh"),  # the user's own shell elsewhere: not ours
        (21, 20, "uv"),
    ]
    assert running_in_terminal(_table(base), vscode) == []
    busy = base + [(13, shell, "uv"), (14, 13, "python3.12")]
    assert running_in_terminal(_table(busy), vscode) == ["uv"]
    # a shell's own helper fork (autosuggestions worker) is not a running command
    helper = base + [(15, shell, "zsh")]
    assert running_in_terminal(_table(helper), vscode) == []
    # ...but a shell that is itself running something is
    nested = base + [(15, shell, "zsh"), (16, 15, "pytest")]
    assert "pytest" in running_in_terminal(_table(nested), vscode)
    server = base + [(17, shell, "/Users/x/.venv/bin/uvicorn")]
    assert running_in_terminal(_table(server), vscode) == ["uvicorn"]
