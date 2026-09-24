"""Replay script beats to know what the tutorial workspace contains at any point.

This is the script-level model of the workspace (files the tutorial writes, processes it starts).
Files created by commands such as `uv init` are not known here; at recording time the typing
engine diffs against the real file on disk, so that gap is harmless.
"""

from tutorial_ai.models.script import BeatDraft, BeatKind, FileSnapshot
from tutorial_ai.security.command_validator import validate_command
from tutorial_ai.tutorial.durations import typed_text

# A write_code beat may type at most this many new lines; bigger changes must be split into
# several beats so the viewer can follow (and the narration keeps pace with the typing).
MAX_TYPED_LINES_PER_BEAT = 20


class WorkspaceState:
    def __init__(
        self,
        files: dict[str, str] | None = None,
        running: list[str] | None = None,
        allowed_commands: set[str] | None = None,
        package_dirs: set[str] | None = None,
    ):
        self.files: dict[str, str] = dict(files or {})
        self.running: list[str] = list(running or [])
        # Executables this project documents (e.g. yt-dlp, ffmpeg), allowed on top of the base list.
        self.allowed_commands: set[str] = set(allowed_commands or ())
        # Folders that are Python packages in the reference repository (they contain __init__.py).
        self.package_dirs: set[str] = set(package_dirs or ())

    @classmethod
    def replay(
        cls, beats: list[BeatDraft], allowed_commands: set[str] | None = None,
        package_dirs: set[str] | None = None,
    ) -> "WorkspaceState":
        state = cls(allowed_commands=allowed_commands, package_dirs=package_dirs)
        for beat in beats:
            state.apply(beat)
        return state

    def copy(self) -> "WorkspaceState":
        return WorkspaceState(self.files, self.running, self.allowed_commands, self.package_dirs)

    def check(self, beat: BeatDraft) -> str | None:
        """Describe why `beat` cannot happen in the current state, or None if it can."""
        match beat.kind:
            case BeatKind.WRITE_CODE:
                before = self.files.get(beat.file)
                if before is not None and before == beat.code_after:
                    return f"write_code on {beat.file} does not change the file"
                before = before or ""
                missing_init = self._missing_package_inits(beat.file)
                if missing_init:
                    return (
                        f"{beat.file} is inside a Python package in the reference repository, but "
                        f"{', '.join(missing_init)} does not exist yet, so `from {missing_init[0].split('/')[0]} "
                        f"import ...` would fail. Create it first (a write_code beat with empty "
                        f"code_after, or `touch {missing_init[0]}`)."
                    )
                typed_lines = typed_text(before, beat.code_after).count("\n")
                if typed_lines > MAX_TYPED_LINES_PER_BEAT:
                    return (
                        f"write_code on {beat.file} types about {typed_lines} new lines in one "
                        f"beat; split it into several write_code beats of at most "
                        f"{MAX_TYPED_LINES_PER_BEAT} new lines, each with its own narration"
                    )
            case BeatKind.DELETE_FILE:
                if beat.file not in self.files:
                    return f"delete_file {beat.file}: file was never created in the tutorial"
            case BeatKind.HIGHLIGHT:
                if beat.file not in self.files:
                    return f"highlight in {beat.file}: file was never created in the tutorial"
                if beat.highlight not in self.files[beat.file]:
                    return (
                        f"highlight text {beat.highlight!r} does not appear in the current "
                        f"content of {beat.file}; copy it exactly"
                    )
            case BeatKind.STOP_PROCESS:
                if not self.running:
                    return "stop_process: no background process is running"
            case BeatKind.TRY_ENDPOINT:
                if not self.running:
                    return (
                        f"try_endpoint {beat.method} {beat.path}: no server is running; start it with a "
                        "background run_command (and show it with open_browser) first"
                    )
            case BeatKind.RUN_COMMAND:
                check = validate_command(beat.command, self.allowed_commands)
                if not check.allowed:
                    return f"command {beat.command!r} is not allowed: {check.reason}"
                if beat.background and self.running:
                    return (
                        f"run_command {beat.command!r} starts a background process while "
                        f"{self.running[-1]!r} is still running; add a stop_process beat first"
                    )
        return None

    def _missing_package_inits(self, path: str) -> list[str]:
        """__init__.py files the reference repo has for the folders of `path`, still absent here."""
        if not path.endswith(".py") or path.endswith("__init__.py"):
            return []
        parts = path.split("/")[:-1]
        missing = []
        for depth in range(1, len(parts) + 1):
            folder = "/".join(parts[:depth])
            init = f"{folder}/__init__.py"
            if folder in self.package_dirs and init not in self.files:
                missing.append(init)
        return missing

    def apply(self, beat: BeatDraft) -> None:
        match beat.kind:
            case BeatKind.WRITE_CODE:
                self.files[beat.file] = beat.code_after
            case BeatKind.DELETE_FILE:
                self.files.pop(beat.file, None)
            case BeatKind.RUN_COMMAND if beat.background:
                self.running.append(beat.command)
            case BeatKind.RUN_COMMAND:
                for created in files_created_by(beat.command or ""):
                    self.files.setdefault(created, "")
            case BeatKind.STOP_PROCESS:
                if self.running:
                    self.running.pop()

    def snapshots(self) -> list[FileSnapshot]:
        return [FileSnapshot(path=p, content=c) for p, c in sorted(self.files.items())]


def check_beats(beats: list[BeatDraft], state: WorkspaceState) -> list[str]:
    """Check a sequence of beats against (a copy of) the state. Returns problems, in order."""
    state = state.copy()
    problems = []
    for index, beat in enumerate(beats, 1):
        problem = state.check(beat)
        if problem:
            problems.append(f"beat {index} ({beat.kind.value}): {problem}")
        state.apply(beat)
    return problems


def files_created_by(command: str) -> list[str]:
    """Files a `touch` in the command line creates (so state knows about empty __init__.py files)."""
    from tutorial_ai.security.command_validator import split_commands

    try:
        groups = split_commands(command)
    except ValueError:
        return []
    created = []
    for tokens in groups:
        if tokens and tokens[0] == "touch":
            created += [t for t in tokens[1:] if not t.startswith("-")]
    return created
