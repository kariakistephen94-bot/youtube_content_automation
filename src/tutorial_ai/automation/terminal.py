"""TerminalController: run commands visibly in VS Code's terminal, and verify them for real.

Commands are typed once into the integrated shell. Its prompt hook reports their exit status;
process disappearance is never treated as proof of success. The explicit verify helper is
reserved for independent smoke-test checks.
"""

import subprocess
import time
from pathlib import Path

from pydantic import BaseModel

from tutorial_ai.automation.macos import AutomationError
from tutorial_ai.automation.vscode import VSCodeController
from tutorial_ai.security.command_validator import validate_command
from tutorial_ai.utils.logging import get_logger

log = get_logger("automation.terminal")

SHELLS = frozenset({"zsh", "bash", "sh", "fish", "tcsh", "ksh", "dash"})


def process_tree() -> dict[int, tuple[int, str]]:
    """pid -> (parent pid, executable name) for every process."""
    result = subprocess.run(["ps", "-axo", "pid=,ppid=,comm="], capture_output=True, text=True)
    table: dict[int, tuple[int, str]] = {}
    for line in result.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else "")
    return table


def running_in_terminal(table: dict[int, tuple[int, str]], vscode_pid: int) -> list[str]:
    """Commands currently running inside the integrated terminals of the VS Code instance
    `vscode_pid`: the children of every shell below it. Empty when the prompt is back.

    The shell's own helper forks (e.g. an async prompt or autosuggestion worker, a `zsh` child
    with no children of its own) do not count as a running command."""
    children: dict[int, list[int]] = {}
    for pid, (ppid, _) in table.items():
        children.setdefault(ppid, []).append(pid)

    def name(pid: int) -> str:
        return Path(table[pid][1]).name.lstrip("-")

    shells: list[int] = []
    stack, seen = [vscode_pid], set()
    while stack:
        for child in children.get(stack.pop(), []):
            if child in seen:
                continue
            seen.add(child)
            stack.append(child)
            if name(child) in SHELLS:
                shells.append(child)
    running: list[str] = []
    for shell in shells:
        for child in children.get(shell, []):
            if name(child) in SHELLS and not children.get(child):
                continue
            running.append(name(child))
    return running


class CommandResult(BaseModel):
    command: str
    exit_code: int
    output: str
    success: bool
    expected: str | None = None


class TerminalController:
    def __init__(self, vscode: VSCodeController) -> None:
        self.vscode = vscode
        self._command = ""

    def run_command(self, command: str, wait_seconds: float = 2.0, *, focus: bool = True) -> None:
        """Validate, then type the command in the integrated terminal and press Enter."""
        check = validate_command(command)
        if not check.allowed:
            raise AutomationError(f"Refusing to run {command!r}: {check.reason}")
        if focus:
            self.vscode.open_terminal()
        if not self.wait_until_idle(15):
            raise AutomationError("The terminal is busy or its shell integration is not ready; recording stopped.")
        self._command = command
        log.info("Running in terminal: %s", command)
        self.vscode.type_text(command, "terminal")
        time.sleep(0.4)
        self.vscode.terminal_status_path.unlink(missing_ok=True)
        self.vscode.keyboard.press("return")
        time.sleep(wait_seconds)

    def running_commands(self) -> list[str]:
        """What the integrated terminal is running right now (empty: the prompt is back)."""
        pid = self.vscode.instance_pid()
        if pid is None:
            return []
        return running_in_terminal(process_tree(), pid)

    def exit_code(self) -> int | None:
        """Exit status reported by the actual integrated shell, never a second execution."""
        try:
            return int(self.vscode.terminal_status_path.read_text().strip())
        except (FileNotFoundError, ValueError):
            return None

    def require_success(self) -> None:
        code = self.exit_code()
        if code is None:
            raise AutomationError("The terminal did not report an exit status; recording stopped.")
        if code != 0:
            hint = " A required command was not found; prepare the environment first." if code == 127 else ""
            raise AutomationError(f"Command {self._command!r} failed with exit code {code}.{hint}")

    def wait_until_idle(self, timeout: float, settle: float = 0.75) -> bool:
        """Wait for the shell's completion report, including fast failures and builtins."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.exit_code() is not None:
                return True
            time.sleep(0.1)
        return self.exit_code() is not None

    def verify(
        self, command: str, cwd: Path, expect_output: str | None = None, timeout: float = 60
    ) -> CommandResult:
        """Run the command as a subprocess and capture its real output."""
        check = validate_command(command)
        if not check.allowed:
            raise AutomationError(f"Refusing to run {command!r}: {check.reason}")
        from tutorial_ai.tutorial.rehearsal import clean_env

        completed = subprocess.run(
            command, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=clean_env()
        )
        output = (completed.stdout + completed.stderr).strip()
        success = completed.returncode == 0 and (expect_output is None or expect_output in output)
        log.info("Verified %r: exit %d, success=%s, output=%r",
                 command, completed.returncode, success, output[:200])
        return CommandResult(
            command=command, exit_code=completed.returncode, output=output,
            success=success, expected=expect_output,
        )
