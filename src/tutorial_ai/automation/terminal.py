"""TerminalController: run commands visibly in VS Code's terminal, and verify them for real.

What the viewer sees is typed into the integrated terminal. Success is not judged from the
screen: for commands that need checking, the same command is also run as a normal subprocess and
its actual output and exit code are captured.
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

    def run_command(self, command: str, wait_seconds: float = 2.0) -> None:
        """Validate, then type the command in the integrated terminal and press Enter."""
        check = validate_command(command)
        if not check.allowed:
            raise AutomationError(f"Refusing to run {command!r}: {check.reason}")
        self.vscode.open_terminal()
        log.info("Running in terminal: %s", command)
        self.vscode.type_text(command, "terminal")
        time.sleep(0.4)
        self.vscode.keyboard.press("return")
        time.sleep(wait_seconds)

    def running_commands(self) -> list[str]:
        """What the integrated terminal is running right now (empty: the prompt is back)."""
        pid = self.vscode.instance_pid()
        if pid is None:
            return []
        return running_in_terminal(process_tree(), pid)

    def wait_until_idle(self, timeout: float, settle: float = 0.75) -> bool:
        """Wait until the terminal's shell has finished the command it is running (no child
        process for `settle` seconds). The viewer sees nothing extra: the check reads the process
        tree, not the screen. Returns False if the command is still running after `timeout`."""
        deadline = time.monotonic() + timeout
        idle_since: float | None = None
        last: list[str] = []
        while time.monotonic() < deadline:
            running = self.running_commands()
            if running:
                idle_since = None
                if running != last:
                    log.info("Terminal is running %s - waiting for it to finish", ", ".join(running))
                    last = running
            else:
                idle_since = idle_since or time.monotonic()
                if time.monotonic() - idle_since >= settle:
                    return True
            time.sleep(0.25)
        return not self.running_commands()

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
