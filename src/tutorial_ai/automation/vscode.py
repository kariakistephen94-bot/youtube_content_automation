"""VSCodeController: drive the real VS Code app on macOS.

Files are opened with the `code` CLI (reliable), and everything the viewer sees being typed is
typed through the keyboard at human speed. The tutorial workspace gets a .vscode/settings.json
that switches off editor features which would corrupt typed code (auto-closing brackets,
auto-indent, suggestions accepted on Enter) - your own VS Code settings are not touched.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from tutorial_ai.automation import macos
from tutorial_ai.automation.typing import HumanTypingEngine, TypingMode
from tutorial_ai.models.base import validate_workspace_path
from tutorial_ai.utils.logging import get_logger

log = get_logger("automation.vscode")

APP_NAME = "Code"  # the process name macOS reports for Visual Studio Code (x11.py maps Linux's "code")
IN_LINUX_SANDBOX = sys.platform.startswith("linux")  # the recording sandbox container (sandbox/)
APP_BUNDLE_NAME = "Visual Studio Code"
BUNDLED_CLI = Path("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code")

# The tutorial runs in its own VS Code profile: no extensions (no Copilot chat, no auto-indent
# from language extensions), no restored windows, and it can be quit/killed without touching the
# user's own VS Code windows or settings.
PROFILE_ROOT = Path("~/.tutorial-ai/vscode").expanduser()
USER_DATA_DIR = PROFILE_ROOT / "user-data"
EXTENSIONS_DIR = PROFILE_ROOT / "extensions"

PROFILE_USER_SETTINGS = {
    "window.restoreWindows": "none",
    "window.newWindowDimensions": "maximized",
    "workbench.startupEditor": "none",
    "workbench.tips.enabled": False,
    "workbench.enableExperiments": False,
    "workbench.welcomePage.walkthroughs.openOnInstall": False,
    "workbench.colorTheme": "Default Dark Modern",
    "update.mode": "none",
    "extensions.autoUpdate": False,
    "extensions.ignoreRecommendations": True,
    "telemetry.telemetryLevel": "off",
    "security.workspace.trust.enabled": False,
    "chat.commandCenter.enabled": False,
    "chat.disableAIFeatures": True,
    "workbench.secondarySideBar.defaultVisibility": "hidden",
    "workbench.statusBar.visible": True,
    "window.commandCenter": False,
    "editor.renderWhitespace": "none",
    "git.openRepositoryInParentFolders": "never",
    "git.autofetch": False,
    "github.gitAuthentication": False,
    "accounts.signInHint": False,
}

TUTORIAL_WORKSPACE_SETTINGS = {
    # Typed text must arrive exactly as written.
    "editor.autoClosingBrackets": "never",
    "editor.autoClosingQuotes": "never",
    "editor.autoClosingComments": "never",
    "editor.autoSurround": "never",
    "editor.autoIndent": "none",
    "editor.formatOnType": False,
    "editor.formatOnPaste": False,
    "editor.formatOnSave": False,
    "editor.linkedEditing": False,
    "editor.quickSuggestions": {"other": "off", "comments": "off", "strings": "off"},
    "editor.suggestOnTriggerCharacters": False,
    "editor.acceptSuggestionOnEnter": "off",
    "editor.tabCompletion": "off",
    "editor.wordBasedSuggestions": "off",
    "editor.parameterHints.enabled": False,
    "editor.inlineSuggest.enabled": False,
    "editor.snippetSuggestions": "none",
    "github.copilot.enable": {"*": False},
    # Readable on video, fewer distractions.
    "editor.fontSize": 18,
    "terminal.integrated.fontSize": 16,
    "editor.minimap.enabled": False,
    "workbench.startupEditor": "none",
    "workbench.tips.enabled": False,
    "terminal.integrated.enablePersistentSessions": False,
    "python.terminal.activateEnvironment": False,
    "files.exclude": {".vscode": True},
}
if IN_LINUX_SANDBOX:
    TUTORIAL_WORKSPACE_SETTINGS.update({
        "editor.fontFamily": "'JetBrains Mono', 'DejaVu Sans Mono', monospace",
        "terminal.integrated.fontFamily": "'JetBrains Mono', 'DejaVu Sans Mono', monospace",
        "terminal.integrated.defaultProfile.linux": "bash",
        "window.titleBarStyle": "custom",
        "window.menuBarVisibility": "compact",
    })

# tutorial-ai's own environment must not leak into the tutorial's terminal (a VIRTUAL_ENV pointing
# at tutorial-ai's venv makes `uv` warn, PYTHONPATH would shadow the viewer's project).
_LEAKY_ENV = {"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH", "PYTHONHOME",
              "NO_COLOR", "COLUMNS", "LINES", "TUTORIAL_AI_SANDBOX"}


def editor_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _LEAKY_ENV and not k.startswith("UV_")}
    venv_bin = os.environ.get("VIRTUAL_ENV", "") + "/bin"
    if venv_bin != "/bin":
        env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if p != venv_bin)
    return env


def find_code_cli() -> str:
    found = shutil.which("code")
    if found:
        return found
    if BUNDLED_CLI.exists():
        return str(BUNDLED_CLI)
    raise macos.AutomationError(
        "Visual Studio Code was not found. Install it in /Applications, or put the `code` "
        "command on your PATH."
    )


class VSCodeController:
    def __init__(self, workspace: Path, typing: HumanTypingEngine | None = None,
                 profile_root: Path = PROFILE_ROOT) -> None:
        self.workspace = workspace.resolve()
        self.typing = typing or HumanTypingEngine()
        self.cli = find_code_cli()
        self._keyboard: macos.Keyboard | None = None
        self.terminal_open = False
        self._user_data_dir = profile_root / "user-data"
        self._extensions_dir = profile_root / "extensions"

    def prepare_profile(self) -> None:
        settings_file = self._user_data_dir / "User" / "settings.json"
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        self._extensions_dir.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(
            json.dumps({**PROFILE_USER_SETTINGS, **TUTORIAL_WORKSPACE_SETTINGS}, indent=2) + "\n",
            encoding="utf-8",
        )

    def quit_profile_instance(self) -> None:
        """Kill any VS Code running on the tutorial profile (never the user's own VS Code)."""
        result = subprocess.run(["pgrep", "-f", f"user-data-dir={self._user_data_dir}"],
                                capture_output=True, text=True)
        pids = [int(p) for p in result.stdout.split() if p.isdigit()]
        for pid in pids:
            subprocess.run(["kill", "-9", str(pid)], capture_output=True)
        if pids:
            log.info("Closed the previous tutorial VS Code instance (%d processes)", len(pids))
            time.sleep(1.5)

    @property
    def keyboard(self) -> macos.Keyboard:
        if self._keyboard is None:
            self._keyboard = macos.Keyboard(APP_NAME, self.instance_pid())
        return self._keyboard

    def instance_pid(self) -> int | None:
        """Main process id of the VS Code running on the tutorial profile."""
        result = subprocess.run(["pgrep", "-f", f"user-data-dir={self._user_data_dir}"],
                                capture_output=True, text=True)
        pids = {int(p) for p in result.stdout.split() if p.isdigit()}
        if not pids:
            return None
        parents = {}
        for pid in pids:
            info = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True).stdout
            parents[pid] = int(info.strip() or 0)
        roots = [pid for pid, ppid in parents.items() if ppid not in pids]
        return min(roots) if roots else min(pids)

    def activate(self) -> None:
        pid = self.instance_pid()
        if pid is None:
            macos.activate_app(APP_BUNDLE_NAME)
            macos.wait_for_frontmost(APP_NAME)
        else:
            macos.activate_pid(pid)
            macos.wait_for_frontmost(APP_NAME, pid=pid)

    # --- workspace --------------------------------------------------------------------------

    def prepare_workspace(self) -> None:
        settings_dir = self.workspace / ".vscode"
        settings_dir.mkdir(parents=True, exist_ok=True)
        (settings_dir / "settings.json").write_text(
            json.dumps(TUTORIAL_WORKSPACE_SETTINGS, indent=2) + "\n", encoding="utf-8"
        )
        log.info("Prepared workspace %s", self.workspace)

    def path(self, relative: str) -> Path:
        return self.workspace / validate_workspace_path(relative)

    # --- window / files ---------------------------------------------------------------------

    def launch(self) -> None:
        """Open the workspace folder in a fresh tutorial-profile VS Code window, in front."""
        macos.require_accessibility()
        self.prepare_profile()
        self.quit_profile_instance()
        log.info("Opening VS Code (tutorial profile) on %s", self.workspace)
        self._code("--new-window", "--disable-workspace-trust", str(self.workspace))
        time.sleep(3.0)
        self._keyboard = None  # a new instance: re-resolve its pid
        self.activate()
        time.sleep(2.0)  # let the window finish loading before typing

    def open_file(self, relative: str) -> None:
        log.info("Opening %s", relative)
        self._code("--reuse-window", str(self.path(relative)))
        self.activate()
        time.sleep(0.8)

    def create_file(self, relative: str) -> None:
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch(exist_ok=True)
        log.info("Created %s", relative)
        self.open_file(relative)

    def focus_editor(self) -> None:
        self.keyboard.shortcut("cmd+1")
        time.sleep(0.3)

    def save_file(self) -> None:
        log.info("Saving")
        self.keyboard.shortcut("cmd+s")
        time.sleep(0.5)

    def press_shortcut(self, combo: str) -> None:
        self.keyboard.shortcut(combo)

    def go_to_line(self, line: int, column: int = 1) -> None:
        """Move the cursor with keys only (no Go-to-Line dialog a stray digit could miss):
        top of file, then down, then right."""
        self.focus_editor()
        self.keyboard.press("escape")  # close any suggestion/hover widget
        self.keyboard.shortcut("cmd+up")  # document start
        time.sleep(0.15)
        self._press_repeated("down", line - 1)
        self.keyboard.press("home")
        if column > 1:
            self._press_repeated("right", column - 1)
        time.sleep(0.15)

    def highlight(self, text: str, line: int | None = None, column: int | None = None) -> None:
        """Select `text` on screen. With a known position the selection is made with shift+arrow
        keys; only as a fallback is the Find box used."""
        first_line = text.splitlines()[0] if text else ""
        if line is not None and column is not None and first_line:
            self.go_to_line(line, column)
            self._press_repeated("right", len(first_line), modifiers=["shift"])
            time.sleep(1.2)  # leave the selection visible while the narration explains it
            return
        self.focus_editor()
        self.keyboard.shortcut("cmd+f")
        time.sleep(0.5)
        self.keyboard.shortcut("cmd+a")
        self.type_text(first_line, "prose")
        time.sleep(0.4)
        self.keyboard.press("return")
        time.sleep(0.3)
        self.keyboard.press("escape")
        time.sleep(1.2)

    def _press_repeated(self, key: str, times: int, modifiers: list[str] | None = None) -> None:
        for _ in range(max(times, 0)):
            self.keyboard.press(key, modifiers)
            time.sleep(0.012)

    def delete_file(self, relative: str) -> None:
        target = self.path(relative)
        if target.exists():
            target.unlink()
            log.info("Deleted %s", relative)
        # Close the (now deleted) editor tab if it is the active one.
        self.keyboard.shortcut("cmd+w")
        time.sleep(0.5)

    def maximize_window(self) -> None:
        """Fill the screen with the VS Code window (best effort, via System Events)."""
        if IN_LINUX_SANDBOX:
            subprocess.run(["wmctrl", "-r", ":ACTIVE:", "-b", "add,maximized_vert,maximized_horz"],
                           capture_output=True, check=False)
            time.sleep(0.8)
            return
        script = (
            'tell application "System Events" to tell process "Code"\n'
            "  set position of front window to {0, 0}\n"
            "  set size of front window to {99999, 99999}\n"
            "end tell"
        )
        subprocess.run(["osascript", "-e", script], capture_output=True, check=False)
        time.sleep(0.8)

    # --- typing -----------------------------------------------------------------------------

    def type_text(self, text: str, mode: TypingMode = "code") -> None:
        strokes = self.typing.plan(text, mode)
        log.info("Typing %d characters (%s, ~%.1fs)", len(strokes), mode,
                 sum(k.delay_before for k in strokes))
        for stroke in strokes:
            # Check before every keystroke: another window can come to the front at any moment
            # (a browser tab, a notification), and a keystroke sent then is lost to it. The check
            # takes part of the human pause instead of adding to it.
            started = time.monotonic()
            self.keyboard.ensure_focus()
            time.sleep(max(0.0, stroke.delay_before - (time.monotonic() - started)))
            self.keyboard.type_char(stroke.char)

    # --- terminal ---------------------------------------------------------------------------

    def open_terminal(self) -> None:
        """Open (or focus) the integrated terminal."""
        log.info("Opening the integrated terminal")
        if self.terminal_open:
            self.run_palette_command("Terminal: Focus Terminal")
        else:
            self.keyboard.shortcut("ctrl+shift+`")  # always creates and focuses a terminal
            self.terminal_open = True
        time.sleep(1.5)  # shell startup

    def run_palette_command(self, name: str) -> None:
        self.keyboard.shortcut("cmd+shift+p")
        time.sleep(0.5)
        self.type_text(name, "prose")
        time.sleep(0.3)
        self.keyboard.press("return")
        time.sleep(0.5)

    def _code(self, *args: str) -> None:
        profile = [f"--user-data-dir={self._user_data_dir}", f"--extensions-dir={self._extensions_dir}"]
        if IN_LINUX_SANDBOX:  # software rendering, no OS keyring prompt; the container is the sandbox
            profile += ["--disable-gpu", "--password-store=basic", "--no-sandbox"]
        result = subprocess.run([self.cli, *profile, *args], capture_output=True, text=True, timeout=60,
                                env=editor_env(), stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            raise macos.AutomationError(f"`code {' '.join(args)}` failed: {result.stderr.strip()}")
