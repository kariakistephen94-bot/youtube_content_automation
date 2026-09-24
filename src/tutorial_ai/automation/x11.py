"""The recording sandbox's input layer: the same interface as macos.py, on an X11 display.

Inside the sandbox container (see sandbox/) VS Code, its terminal and Chrome run on a virtual
screen (Xvfb) that nobody else uses, so recording never touches the user's own keyboard, mouse or
windows. Keystrokes and pointer moves go through xdotool (XTEST). macos.py re-exports this module
on Linux, so every `macos.X` caller works unchanged.

macOS shortcuts are translated: VS Code on Linux uses Ctrl where macOS uses Cmd, and Cmd+arrows
become Home/End style moves.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path

from tutorial_ai.automation.macos import AutomationError
from tutorial_ai.utils.logging import get_logger

log = get_logger("automation.x11")

# xdotool key names for the keys KEY_CODES in macos.py knows.
KEYSYMS = {
    "return": "Return", "enter": "Return", "tab": "Tab", "space": "space", "delete": "BackSpace",
    "backspace": "BackSpace", "escape": "Escape", "esc": "Escape", "left": "Left", "right": "Right",
    "down": "Down", "up": "Up", "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next",
    "`": "grave", "-": "minus", "=": "equal", "[": "bracketleft", "]": "bracketright",
    "\\": "backslash", ";": "semicolon", "'": "apostrophe", ",": "comma", ".": "period", "/": "slash",
}  # fmt: skip
MODIFIERS = {
    "cmd": "ctrl", "command": "ctrl", "ctrl": "ctrl", "control": "ctrl",
    "shift": "shift", "alt": "alt", "option": "alt",
}
# macOS navigation shortcuts with a different Linux spelling (VS Code's default keybindings).
CMD_MOVES = {"up": "ctrl+Home", "down": "ctrl+End", "left": "Home", "right": "End"}
# Process name (/proc/<pid>/comm) -> the app name callers compare against (as on macOS).
APP_NAMES = {"code": "Code", "chrome": "Google Chrome", "google-chrome": "Google Chrome"}


def _xdotool(*args: str, check: bool = True) -> str:
    result = subprocess.run(["xdotool", *args], capture_output=True, text=True, timeout=10)
    if check and result.returncode != 0:
        raise AutomationError(f"xdotool {' '.join(args)} failed: {result.stderr.strip() or result.returncode}")
    return result.stdout.strip()


def require_macos() -> None:
    """On Linux the X display plays the role macOS plays elsewhere."""
    require_accessibility()


def accessibility_trusted() -> bool:
    return bool(os.environ.get("DISPLAY")) and shutil.which("xdotool") is not None


def require_accessibility() -> None:
    if not os.environ.get("DISPLAY"):
        raise AutomationError("No X display (DISPLAY is not set). Desktop automation on Linux runs "
                              "inside the recording sandbox: `tutorial-ai record <project> --sandbox`.")
    if shutil.which("xdotool") is None:
        raise AutomationError("xdotool is not installed (it is part of the sandbox image).")


def app_name(pid: int) -> str:
    try:
        comm = Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return APP_NAMES.get(comm, comm)


def front_application() -> tuple[int | None, str]:
    """(pid, name) of the app owning the focused window."""
    try:
        out = _xdotool("getactivewindow", "getwindowpid", check=False)
    except (OSError, subprocess.SubprocessError):
        return None, ""
    if not out.isdigit():
        return None, ""
    pid = int(out)
    return pid, app_name(pid)


def frontmost_app() -> str:
    return front_application()[1]


def frontmost_pid() -> int | None:
    return front_application()[0]


def _windows_of(pid: int) -> list[str]:
    out = _xdotool("search", "--onlyvisible", "--pid", str(pid), check=False)
    return [w for w in out.split() if w.isdigit()]


def activate_pid(pid: int) -> None:
    windows = _windows_of(pid)
    if windows:
        _xdotool("windowactivate", "--sync", windows[-1], check=False)


APP_PROCESSES = {"Visual Studio Code": "code", "Code": "code", "Google Chrome": "chrome"}


def activate_app(name: str) -> None:
    comm = APP_PROCESSES.get(name)
    if comm is None:
        return
    pids = sorted(int(p) for p in subprocess.run(["pgrep", "-x", comm], capture_output=True,
                                                 text=True).stdout.split() if p.isdigit())
    for pid in pids:  # the main process owns the windows; helpers have none
        if _windows_of(pid):
            activate_pid(pid)
            return


def wait_for_frontmost(name: str, timeout: float = 20.0, pid: int | None = None) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        front_pid, front_name = front_application()
        if (pid is not None and front_pid == pid) or (pid is None and front_name == name):
            return
        time.sleep(0.25)
    raise AutomationError(f"{name} did not come to the front within {timeout:.0f}s")


def main_display_size() -> tuple[float, float]:
    out = _xdotool("getdisplaygeometry")
    width, height = out.split()
    return float(width), float(height)


def backing_scale() -> float:
    return 1.0  # Xvfb has no HiDPI scaling


def key_combo(key: str, modifiers: list[str] | None = None) -> str:
    """'up' + ['cmd'] -> 'ctrl+Home'; 's' + ['cmd'] -> 'ctrl+s'; '`' + ['ctrl', 'shift'] -> 'ctrl+shift+grave'."""
    mods = [m.lower() for m in modifiers or []]
    key = key.lower()
    if "cmd" in mods or "command" in mods:
        rest = [m for m in mods if m not in ("cmd", "command")]
        if key in CMD_MOVES and not any(m in ("ctrl", "control", "alt", "option") for m in rest):
            return "+".join([*(MODIFIERS[m] for m in rest), CMD_MOVES[key]])
    names = []
    for mod in mods:
        if mod not in MODIFIERS:
            raise AutomationError(f"Unknown modifier {mod!r}")
        if MODIFIERS[mod] not in names:
            names.append(MODIFIERS[mod])
    if len(key) == 1 and (key.isalnum()):
        sym = key
    elif key in KEYSYMS:
        sym = KEYSYMS[key]
    else:
        raise AutomationError(f"Unknown key {key!r}")
    return "+".join([*names, sym])


class Keyboard:
    """Same contract as macos.Keyboard: every public method first checks our app is in front."""

    def __init__(self, expected_app: str, expected_pid: int | None = None) -> None:
        require_accessibility()
        self.expected_app = expected_app
        self.expected_pid = expected_pid

    def ensure_focus(self) -> None:
        pid, current = front_application()
        wrong_app = current != self.expected_app
        wrong_instance = self.expected_pid is not None and pid != self.expected_pid
        if wrong_app or wrong_instance:
            what = repr(current or "no window") if wrong_app else f"another {self.expected_app} window"
            raise AutomationError(f"Stopped typing: {what} is in front instead of {self.expected_app!r}.")

    def type_char(self, char: str) -> None:
        if char == "\n":
            _xdotool("key", "--clearmodifiers", "Return")
            return
        _xdotool("type", "--clearmodifiers", "--delay", "0", "--", char)

    def press(self, key: str, modifiers: list[str] | None = None) -> None:
        self.ensure_focus()
        _xdotool("key", "--clearmodifiers", key_combo(key, modifiers))

    def shortcut(self, combo: str) -> None:
        *mods, key = combo.split("+") if combo != "+" else ["+"]
        self.press(key, mods)


class Mouse:
    """The real X pointer, gliding like a hand on a mouse (shown in the recording)."""

    def __init__(self) -> None:
        require_accessibility()

    @staticmethod
    def position() -> tuple[float, float]:
        values = dict(line.split("=", 1) for line in _xdotool("getmouselocation", "--shell").splitlines() if "=" in line)
        return float(values.get("X", 0)), float(values.get("Y", 0))

    def move_to(self, x: float, y: float, duration: float = 0.55) -> None:
        import math

        x0, y0 = self.position()
        steps = max(10, min(40, int(math.hypot(x - x0, y - y0) / 12)))
        for index in range(1, steps + 1):
            t = index / steps
            eased = t * t * (3 - 2 * t)
            _xdotool("mousemove", str(round(x0 + (x - x0) * eased)), str(round(y0 + (y - y0) * eased)))
            time.sleep(duration / steps)

    def click(self, x: float, y: float) -> None:
        _xdotool("mousemove", str(round(x)), str(round(y)))
        _xdotool("mousedown", "1")
        time.sleep(0.07)
        _xdotool("mouseup", "1")
