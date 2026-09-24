"""Low-level macOS input: keystrokes via Quartz CGEvents, app focus via AppKit.

Characters are sent as Unicode strings (not key codes), so any character - brackets, quotes,
non-ASCII - arrives exactly as written regardless of keyboard layout. Requires the macOS
Accessibility permission for the app that runs this program (Terminal, iTerm, VS Code or Claude).
"""

import math
import re
import subprocess
import sys
import time

from tutorial_ai.utils.logging import get_logger

log = get_logger("automation.macos")


class AutomationError(RuntimeError):
    """Automation cannot continue safely (missing permission, focus lost, ...)."""


if sys.platform == "darwin":
    import ApplicationServices
    import Quartz
    from AppKit import NSWorkspace

# Virtual key codes (US layout positions; used only for shortcuts and special keys).
KEY_CODES = {
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51,
    "escape": 53, "esc": 53, "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121, "`": 50, "-": 27, "=": 24,
    "[": 33, "]": 30, "\\": 42, ";": 41, "'": 39, ",": 43, ".": 47, "/": 44,
    "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3, "g": 5, "h": 4, "i": 34, "j": 38,
    "k": 40, "l": 37, "m": 46, "n": 45, "o": 31, "p": 35, "q": 12, "r": 15, "s": 1, "t": 17,
    "u": 32, "v": 9, "w": 13, "x": 7, "y": 16, "z": 6,
    "0": 29, "1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22, "7": 26, "8": 28, "9": 25,
}  # fmt: skip

_MODIFIERS = {
    "cmd": "kCGEventFlagMaskCommand", "command": "kCGEventFlagMaskCommand",
    "ctrl": "kCGEventFlagMaskControl", "control": "kCGEventFlagMaskControl",
    "shift": "kCGEventFlagMaskShift",
    "alt": "kCGEventFlagMaskAlternate", "option": "kCGEventFlagMaskAlternate",
}  # fmt: skip

ACCESSIBILITY_HELP = (
    "macOS has not given this program permission to type into other apps.\n"
    "Open System Settings > Privacy & Security > Accessibility and turn ON the app you are "
    "running tutorial-ai from (Claude, Terminal, iTerm or Visual Studio Code). If it is not "
    "listed, click + and add it. Then quit and reopen that app and run the command again."
)


def require_macos() -> None:
    if sys.platform != "darwin":
        raise AutomationError("Desktop automation currently supports macOS only.")


def accessibility_trusted() -> bool:
    require_macos()
    return bool(ApplicationServices.AXIsProcessTrusted())


def require_accessibility() -> None:
    if not accessibility_trusted():
        raise AutomationError(ACCESSIBILITY_HELP)


def front_application() -> tuple[int | None, str]:
    """(pid, name) of the app that receives keystrokes right now.

    Not NSWorkspace.frontmostApplication(): that value is only refreshed by the main thread's
    run loop, which a CLI or API worker thread never runs, so it keeps reporting whichever app
    was in front when the program started - a focus check built on it never notices another
    window coming to the front. lsappinfo asks LaunchServices directly (~20 ms)."""
    require_macos()
    try:
        asn = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True, timeout=5).stdout.strip()
        if asn:
            info = subprocess.run(["lsappinfo", "info", "-only", "pid", "-only", "name", asn],
                                  capture_output=True, text=True, timeout=5).stdout
            pid = re.search(r'"pid"\s*=\s*(\d+)', info)
            name = re.search(r'"LSDisplayName"\s*=\s*"([^"]*)"', info)
            if pid:
                return int(pid.group(1)), name.group(1) if name else ""
    except (OSError, subprocess.SubprocessError):
        pass
    app = NSWorkspace.sharedWorkspace().frontmostApplication()  # fallback: may be stale
    return (int(app.processIdentifier()), str(app.localizedName())) if app else (None, "")


def frontmost_app() -> str:
    return front_application()[1]


def frontmost_pid() -> int | None:
    return front_application()[0]


def activate_app(name: str) -> None:
    subprocess.run(["osascript", "-e", f'tell application "{name}" to activate'], check=False)


def activate_pid(pid: int) -> None:
    """Bring a specific process's windows to the front (needed when two copies of an app run)."""
    script = f'tell application "System Events" to set frontmost of (first process whose unix id is {pid}) to true'
    subprocess.run(["osascript", "-e", script], capture_output=True, check=False)


def main_display_size() -> tuple[float, float]:
    """Width and height of the main display in points."""
    require_macos()
    bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
    return float(bounds.size.width), float(bounds.size.height)


def backing_scale() -> float:
    """Device pixels per point on the main display (2.0 on Retina screens)."""
    require_macos()
    from AppKit import NSScreen

    screen = NSScreen.mainScreen()
    return float(screen.backingScaleFactor()) if screen else 1.0


def wait_for_frontmost(name: str, timeout: float = 20.0, pid: int | None = None) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pid is not None:
            if frontmost_pid() == pid:
                return
        elif frontmost_app() == name:
            return
        time.sleep(0.25)
    raise AutomationError(f"{name} did not come to the front within {timeout:.0f}s")


class Keyboard:
    """Posts keyboard events. Every public method first checks the expected app is in front,
    so keystrokes never go to the wrong window."""

    def __init__(self, expected_app: str, expected_pid: int | None = None) -> None:
        require_accessibility()
        self.expected_app = expected_app
        self.expected_pid = expected_pid  # distinguishes our VS Code instance from the user's own

    def ensure_focus(self) -> None:
        pid, current = front_application()
        wrong_app = current != self.expected_app
        wrong_instance = self.expected_pid is not None and pid != self.expected_pid
        if wrong_app or wrong_instance:
            what = repr(current) if wrong_app else f"another {self.expected_app} window (not the tutorial's)"
            raise AutomationError(
                f"Stopped typing: {what} is in front instead of {self.expected_app!r}. "
                "Did you click another window? Nothing was typed into it."
            )

    def type_char(self, char: str) -> None:
        if char == "\n":
            self._key(KEY_CODES["return"])
            return
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
            Quartz.CGEventSetFlags(event, 0)
            Quartz.CGEventKeyboardSetUnicodeString(event, len(char.encode("utf-16-le")) // 2, char)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def press(self, key: str, modifiers: list[str] | None = None) -> None:
        self.ensure_focus()
        code = KEY_CODES.get(key.lower())
        if code is None:
            raise AutomationError(f"Unknown key {key!r}")
        flags = 0
        for modifier in modifiers or []:
            flags |= getattr(Quartz, _MODIFIERS[modifier.lower()])
        self._key(code, flags)

    def shortcut(self, combo: str) -> None:
        """'cmd+s', 'ctrl+shift+`' ..."""
        *mods, key = combo.split("+") if combo != "+" else ["+"]
        self.press(key, mods)

    @staticmethod
    def _key(code: int, flags: int = 0) -> None:
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, code, down)
            Quartz.CGEventSetFlags(event, flags)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


class Mouse:
    """Moves the real pointer and clicks with Quartz events, so the screen recording shows the
    cursor travelling to a button before it is pressed (coordinates in screen points)."""

    def __init__(self) -> None:
        require_accessibility()

    @staticmethod
    def position() -> tuple[float, float]:
        location = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        return float(location.x), float(location.y)

    def move_to(self, x: float, y: float, duration: float = 0.55) -> None:
        """Glide from the current position in an eased curve, like a hand moving a mouse."""
        x0, y0 = self.position()
        distance = math.hypot(x - x0, y - y0)
        steps = max(10, min(60, int(distance / 10)))
        for index in range(1, steps + 1):
            t = index / steps
            eased = t * t * (3 - 2 * t)  # smoothstep: slow start and stop
            self._post(Quartz.kCGEventMouseMoved, x0 + (x - x0) * eased, y0 + (y - y0) * eased)
            time.sleep(duration / steps)

    def click(self, x: float, y: float) -> None:
        self._post(Quartz.kCGEventLeftMouseDown, x, y)
        time.sleep(0.07)
        self._post(Quartz.kCGEventLeftMouseUp, x, y)

    @staticmethod
    def _post(kind: int, x: float, y: float) -> None:
        event = Quartz.CGEventCreateMouseEvent(None, kind, (x, y), Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


if sys.platform.startswith("linux"):
    # The recording sandbox (sandbox/Dockerfile): the same interface on its own virtual X display.
    # Functions above that call front_application() etc. pick up these replacements at call time.
    from tutorial_ai.automation.x11 import (  # noqa: E402,F401,F811
        Keyboard,
        Mouse,
        accessibility_trusted,
        activate_app,
        activate_pid,
        backing_scale,
        front_application,
        frontmost_app,
        frontmost_pid,
        main_display_size,
        require_accessibility,
        require_macos,
        wait_for_frontmost,
    )
