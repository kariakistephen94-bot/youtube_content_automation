"""ActionExecutor: perform compiled actions in the real VS Code (and browser), one at a time.

Each action returns when it is visibly complete. Anything that means "the screen is not what we
expect" (VS Code lost focus, a server never came up, the docs page has no such endpoint) raises
AutomationError so the recording session can pause instead of typing into the wrong place.
"""

import subprocess
import time
import urllib.error
import urllib.request

from tutorial_ai.automation import macos
from tutorial_ai.automation.browser import BrowserController, describe_request
from tutorial_ai.automation.terminal import TerminalController
from tutorial_ai.automation.vscode import VSCodeController
from tutorial_ai.models.action import Action, ActionType
from tutorial_ai.utils.logging import get_logger

log = get_logger("automation.executor")

FIND_TYPING_SPEED = "prose"
SERVER_READY_TIMEOUT = 90.0  # a first `uv run` may still have to build the project
COMMAND_TIMEOUT = 300.0  # `uv add` on a slow connection can take minutes; the viewer waits too
BROWSER_ACTIONS = (
    ActionType.OPEN_BROWSER, ActionType.NAVIGATE_BROWSER, ActionType.TRY_ENDPOINT,
    ActionType.CLICK_BROWSER, ActionType.WAIT_FOR_BROWSER,
)


class ActionExecutor:
    def __init__(self, vscode: VSCodeController, terminal: TerminalController,
                 browser: BrowserController | None = None) -> None:
        self.vscode = vscode
        self.terminal = terminal
        self.browser = browser  # None: open_browser falls back to the default browser, no interaction
        self._browser_in_front = False
        self._terminal_prepared = False

    def execute(self, action: Action) -> None:
        terminal_prepared = self._terminal_prepared
        self._terminal_prepared = False
        p = action.parameters.model_dump()
        log.info("Action %s %s %s", action.id, action.type.value, _short(p))
        stays_in_browser = action.type in BROWSER_ACTIONS or action.type is ActionType.WAIT or (
            action.type is ActionType.SCROLL and p.get("target") == "browser"
        )
        if self._browser_in_front and not stays_in_browser:
            self._back_to_vscode()

        match action.type:
            case ActionType.OPEN_VSCODE:
                self.vscode.launch()
            case ActionType.CREATE_FILE:
                self.vscode.create_file(p["path"])
            case ActionType.OPEN_FILE:
                self.vscode.open_file(p["path"])
            case ActionType.DELETE_FILE:
                self.vscode.delete_file(p["path"])
            case ActionType.TYPE_CODE:
                self.vscode.focus_editor()
                self.vscode.type_text(p["text"], "code")
            case ActionType.TYPE_TEXT:
                self.vscode.type_text(p["text"], "prose")
            case ActionType.MOVE_CURSOR:
                self.vscode.go_to_line(p["line"])
            case ActionType.KEYBOARD_SHORTCUT:
                for _ in range(p.get("presses", 1)):
                    self.vscode.press_shortcut("+".join(p["keys"]))
                    time.sleep(0.03)
            case ActionType.PRESS_KEY:
                for _ in range(p.get("presses", 1)):
                    self.vscode.keyboard.press(p["key"])
                    time.sleep(0.05)
            case ActionType.SAVE_FILE:
                self.vscode.save_file()
            case ActionType.HIGHLIGHT_TEXT | ActionType.SELECT_TEXT:
                self.vscode.highlight(p["text"], p.get("line"), p.get("column"))
            case ActionType.OPEN_TERMINAL | ActionType.FOCUS_TERMINAL:
                self.vscode.open_terminal()
                self._terminal_prepared = True
            case ActionType.RUN_COMMAND:
                self._run_command(p, focus=not terminal_prepared)
            case ActionType.WAIT:
                time.sleep(p["seconds"])
            case ActionType.OPEN_BROWSER:
                self._open_browser(p.get("url"))
            case ActionType.NAVIGATE_BROWSER:
                self._require_browser().navigate(p["url"])
                self._browser_in_front = True
            case ActionType.TRY_ENDPOINT:
                self._try_endpoint(p)
            case ActionType.CLICK_BROWSER:
                self._require_browser().click(p["selector"])
            case ActionType.WAIT_FOR_BROWSER:
                self._require_browser().wait_for(p.get("selector"), p["timeout_seconds"])
            case ActionType.SCROLL if p.get("target") == "browser" and self.browser is not None:
                self.browser.scroll(p["direction"], p["amount"])
            case ActionType.SCROLL:
                log.warning("Action %s is not supported in V1; skipped", action.type.value)
            case _:
                log.warning("Action %s ignored by the executor", action.type.value)

        if action.wait_after_seconds:
            time.sleep(action.wait_after_seconds)

    # --- terminal ---------------------------------------------------------------------------

    def _run_command(self, p: dict, *, focus: bool = True) -> None:
        command = p["command"]
        background = bool(p.get("background"))
        self.terminal.run_command(command, wait_seconds=0.0 if background else 1.0, focus=focus)
        if background:
            url = p.get("wait_url")
            if url:
                self._wait_for_url(url, command)
            else:
                time.sleep(4.0)
                if self.terminal.exit_code() is not None:
                    self.terminal.require_success()
                    raise macos.AutomationError(f"Background command {command!r} exited instead of staying running.")
            return
        # The shell reports completion and the real exit status, even for instant failures.
        started = time.monotonic()
        if not self.terminal.wait_until_idle(COMMAND_TIMEOUT):
            raise macos.AutomationError(
                f"{command!r} is still running after {COMMAND_TIMEOUT:.0f}s. Check the terminal in "
                "VS Code; if the command needs that long, run it in the background instead."
            )
        self.terminal.require_success()
        elapsed = time.monotonic() - started
        if elapsed > 3:
            log.info("%r finished after %.0fs", command, elapsed)
        remaining = _budget(p) - elapsed  # short commands stay on screen for a moment
        if remaining > 0:
            time.sleep(remaining)

    def _wait_for_url(self, url: str, command: str) -> None:
        deadline = time.monotonic() + SERVER_READY_TIMEOUT
        while time.monotonic() < deadline:
            if self.terminal.exit_code() is not None:
                self.terminal.require_success()
                raise macos.AutomationError(f"Server command {command!r} exited before answering at {url}.")
            try:
                with urllib.request.urlopen(url, timeout=3) as response:
                    if response.status < 500:
                        log.info("Server is up: %s", url)
                        return
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    return
            except Exception:
                pass
            time.sleep(0.5)
        raise macos.AutomationError(
            f"The server started with {command!r} did not answer at {url} within "
            f"{SERVER_READY_TIMEOUT:.0f}s. Check the terminal in VS Code for the error."
        )

    # --- browser ----------------------------------------------------------------------------

    def _open_browser(self, url: str | None) -> None:
        if not url:
            return
        if self.browser is None:
            log.info("Opening the default browser at %s", url)
            subprocess.run(["open", url], check=False)
        else:
            self.browser.open(url)
        self._browser_in_front = True
        time.sleep(3.0)

    def _try_endpoint(self, p: dict) -> None:
        browser = self._require_browser()
        log.info("Trying %s in the docs", describe_request(p["method"], p["path"], p.get("params"), p.get("body")))
        fresh = browser.open(p["url"])
        self._browser_in_front = True
        # A page that was already open shows the docs as they were: reload to pick up the edits.
        browser.try_endpoint(p["method"], p["path"], p.get("params") or {}, p.get("body"),
                             p.get("sample_files") or {}, refresh=not fresh)

    def _require_browser(self) -> BrowserController:
        if self.browser is None:
            raise macos.AutomationError(
                "This scene interacts with the browser, but browser automation is not available "
                "(recording without a BrowserController)."
            )
        return self.browser

    def _back_to_vscode(self) -> None:
        self.vscode.activate()
        time.sleep(0.5)
        self._browser_in_front = False


def _budget(p: dict) -> float:
    """Seconds a finished foreground command stays on screen (installs longer than `echo`)."""
    command = p["command"].lower()
    if any(w in command for w in ("install", " add ", " sync", "init", "pytest", "test")):
        return 6.0
    return 2.5


def _short(p: dict) -> str:
    if "method" in p and "path" in p:
        return f"{p['method'].upper()} {p['path']}"
    text = p.get("text") or p.get("command") or p.get("path") or p.get("url") or ""
    text = text.replace("\n", "\\n")
    return text[:60] + ("…" if len(text) > 60 else "")
