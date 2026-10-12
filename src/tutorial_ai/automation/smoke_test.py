"""The Phase 5 smoke test: open VS Code, create main.py, type code, save, run it, verify."""

import shutil
import time
from collections.abc import Callable

from pydantic import BaseModel

from tutorial_ai.automation import macos
from tutorial_ai.automation.terminal import TerminalController
from tutorial_ai.automation.typing import HumanTypingEngine, TypingProfile, TypingSpeed
from tutorial_ai.automation.vscode import VSCodeController
from tutorial_ai.config import Settings

DEFAULT_TEXT = 'print("Hello from AI")'
COMMAND = "python3 main.py"  # macOS ships python3, not python


class SmokeTestResult(BaseModel):
    success: bool
    workspace: str
    file_content: str
    command: str
    command_output: str
    exit_code: int


def run_vscode_smoke_test(
    settings: Settings,
    speed: TypingSpeed = TypingSpeed.NORMAL,
    text: str = DEFAULT_TEXT,
    step: Callable[[str], None] = print,
    countdown_seconds: float = 3.0,
) -> SmokeTestResult:
    """Raises macos.AutomationError when automation must stop (permission, focus lost...)."""
    macos.require_accessibility()

    workspace_root = settings.workspace_directory.resolve()
    build = workspace_root / "vscode-test" / "tutorial_build"
    if build.exists() and build.resolve().is_relative_to(workspace_root):
        shutil.rmtree(build)  # a fresh, throwaway teaching workspace every run
    build.mkdir(parents=True)

    vscode = VSCodeController(build, HumanTypingEngine(TypingProfile.preset(speed), seed=None))
    terminal = TerminalController(vscode)

    step(f"Starting in {countdown_seconds:.0f} seconds. Don't touch the keyboard or mouse; "
         "clicking another window stops the typing safely.")
    time.sleep(countdown_seconds)
    vscode.prepare_workspace()
    step("1. Opening VS Code")
    vscode.launch()
    step("2. Creating main.py")
    vscode.create_file("main.py")
    step(f"3. Typing {text!r}")
    vscode.type_text(text + "\n")
    step("4. Saving")
    vscode.save_file()
    step(f"5. Opening the terminal and running {COMMAND!r}")
    terminal.run_command(COMMAND)
    if not terminal.wait_until_idle(60):
        raise macos.AutomationError("The VS Code test command did not finish within 60 seconds.")
    terminal.require_success()

    step("6. Checking the result")
    saved = (build / "main.py").read_text(encoding="utf-8")
    expected = "Hello from AI" if text == DEFAULT_TEXT else None
    result = terminal.verify(COMMAND, build, expect_output=expected)
    return SmokeTestResult(
        success=saved.strip() == text.strip() and result.success,
        workspace=str(build),
        file_content=saved,
        command=COMMAND,
        command_output=result.output,
        exit_code=result.exit_code,
    )



