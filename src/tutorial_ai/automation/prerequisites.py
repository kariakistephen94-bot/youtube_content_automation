"""Check base toolchains before touching the desktop or recording a scene."""
import shutil

from tutorial_ai.automation.macos import AutomationError
from tutorial_ai.automation.vscode import editor_env
from tutorial_ai.security.command_validator import executables_in

# Project-installed CLIs (next, pytest, etc.) may legitimately appear only after an install beat.
BASE_TOOLS = {"node", "npm", "npx", "python", "python3", "uv", "uvx", "git", "ffmpeg", "ffprobe", "yt-dlp"}


def check_prerequisites(commands: list[str]) -> None:
    required = {exe for command in commands for exe in executables_in(command)} & BASE_TOOLS
    path = editor_env().get("PATH", "")
    missing = sorted(tool for tool in required if not shutil.which(tool, path=path))
    if missing:
        raise AutomationError(
            "Missing recording prerequisites: " + ", ".join(missing) + ". "
            "Rebuild the sandbox image to install its supported tools, or install these tools "
            "on the selected recording machine. No recording has started."
        )
