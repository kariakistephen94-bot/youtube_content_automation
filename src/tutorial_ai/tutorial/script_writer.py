"""ScriptWriter: TutorialPlan -> TutorialScript, one section at a time.

Sections are written in order. Each AI call sees the exact workspace files produced by the
previous sections, so the code builds up consistently. After each section the beats are checked
against the replayed workspace state; problems are sent back to the AI once for repair.
The script is handed to `on_section_done` after every section so progress survives a crash and
generation can resume where it stopped.
"""

import difflib
from collections.abc import Callable
from pathlib import Path

from tutorial_ai.models.repository import RepositoryAnalysis
from tutorial_ai.models.script import (
    BeatDraft,
    BeatKind,
    ConvergenceItem,
    ConvergenceStatus,
    FileSnapshot,
    ScriptBeat,
    ScriptSection,
    ScriptSectionRequest,
    TutorialScript,
)
from tutorial_ai.models.tutorial import TutorialPlan, TutorialSection
from tutorial_ai.providers.ai.base import AIProvider
from tutorial_ai.repository.scanner import FileCategory, ScanResult, scan_repository
from tutorial_ai.tutorial.workspace_state import WorkspaceState, check_beats
from tutorial_ai.utils.logging import get_logger
from tutorial_ai.utils.text import estimate_tokens

log = get_logger("tutorial.script_writer")

DEFAULT_MAX_REFERENCE_TOKENS = 40_000
SIMILAR_THRESHOLD = 0.85
_CONVERGENCE_CATEGORIES = {FileCategory.SOURCE, FileCategory.TEST, FileCategory.MANIFEST}


class ScriptError(RuntimeError):
    pass


class ScriptWriter:
    def __init__(
        self,
        provider: AIProvider,
        analysis: RepositoryAnalysis,
        plan: TutorialPlan,
        *,
        max_file_bytes: int = 200_000,
        max_reference_tokens: int = DEFAULT_MAX_REFERENCE_TOKENS,
        max_repair_attempts: int = 2,
        rehearsal_feedback: dict[int, list[str]] | None = None,
    ) -> None:
        self._provider = provider
        self._analysis = analysis
        self._plan = plan
        self._max_reference_tokens = max_reference_tokens
        self._max_repair_attempts = max_repair_attempts
        self._repo_root = Path(analysis.source.local_path)
        self._scan: ScanResult = scan_repository(self._repo_root, max_file_bytes)
        self._repository_summary = summarize_analysis(analysis)
        self._allowed_commands = project_commands(analysis)
        self._package_dirs = {
            f.path.rsplit("/", 1)[0] for f in self._scan.files if f.path.endswith("/__init__.py")
        }
        # Problems found when the previous version of a section was actually run (rehearsal).
        self._rehearsal_feedback = rehearsal_feedback or {}

    def write(
        self,
        existing: TutorialScript | None = None,
        from_section: int | None = None,
        on_section_done: Callable[[TutorialScript], None] | None = None,
    ) -> TutorialScript:
        script = self._resume_point(existing, from_section)
        state = WorkspaceState.replay(script.beats, self._allowed_commands, self._package_dirs)

        for section in self._plan.sections[len(script.sections):]:
            log.info("Writing section %d/%d: %s",
                     section.section_number, len(self._plan.sections), section.title)
            written, warnings = self._write_section(section, script, state)
            for beat in written.beats:
                state.apply(beat)
            script.sections.append(written)
            script.warnings += warnings
            if on_section_done:
                on_section_done(script)

        script.convergence = self.convergence_report(state)
        return script

    def _resume_point(
        self, existing: TutorialScript | None, from_section: int | None
    ) -> TutorialScript:
        if existing is None:
            return TutorialScript(title=self._plan.title)
        script = existing.model_copy(deep=True)
        script.title = self._plan.title
        keep = len(script.sections) if from_section is None else from_section - 1
        script.sections = script.sections[: max(keep, 0)]
        for written, planned in zip(script.sections, self._plan.sections):
            if (written.section_number, written.title) != (planned.section_number, planned.title):
                raise ScriptError(
                    f"script.json section {written.section_number} ({written.title!r}) does not "
                    "match the current plan. Re-run with --restart."
                )
        # Re-check the sections we keep against the current rules, so warnings from an older run
        # (e.g. a command that is allowed for this project now) do not linger.
        state = WorkspaceState(allowed_commands=self._allowed_commands, package_dirs=self._package_dirs)
        script.warnings = []
        for kept_section in script.sections:
            kept_section.beats = fix_kept_section(kept_section.beats, state)
            problems = check_beats(kept_section.beats, state)
            script.warnings += [f"section {kept_section.section_number}: {p}" for p in problems]
            for beat in kept_section.beats:
                state.apply(beat)
        if script.sections:
            log.info("Resuming after section %d", script.sections[-1].section_number)
        return script

    def _write_section(
        self, section: TutorialSection, script: TutorialScript, state: WorkspaceState
    ) -> tuple[ScriptSection, list[str]]:
        feedback: list[str] = [
            f"When the previous version of this section was run for real: {problem}"
            for problem in self._rehearsal_feedback.get(section.section_number, [])
        ]
        problems: list[str] = []
        for attempt in range(1 + self._max_repair_attempts):
            request = ScriptSectionRequest(
                plan=self._plan,
                section_number=section.section_number,
                repository_summary=self._repository_summary,
                previous_state_summaries=[s.state_summary for s in script.sections],
                workspace_files=state.snapshots(),
                running_processes=list(state.running),
                reference_files=self.reference_files(section, state),
                feedback=feedback,
            )
            draft = self._provider.create_script_section(request)
            problems = check_beats(draft.beats, state)
            if not problems:
                break
            log.warning(
                "Section %d attempt %d has %d problem(s): %s",
                section.section_number, attempt + 1, len(problems), "; ".join(problems),
            )
            feedback = problems

        drafts = insert_server_stops(draft.beats, state)
        if len(drafts) != len(draft.beats):
            problems = check_beats(drafts, state)  # the stop beats resolve some warnings
        beats = [
            ScriptBeat(id=f"s{section.section_number:02d}_b{index:02d}", **beat.model_dump())
            for index, beat in enumerate(drafts, 1)
        ]
        written = ScriptSection(
            section_number=section.section_number,
            title=section.title,
            beats=beats,
            state_summary=draft.state_summary,
        )
        return written, [f"section {section.section_number}: {p}" for p in problems]

    def reference_files(self, section: TutorialSection, state: WorkspaceState) -> list[FileSnapshot]:
        """Original repository files relevant to this section, within the token budget.

        Priority: files the section touches, then the repository counterparts of files already in
        the workspace, then manifests (so dependency versions and tooling stay accurate).
        """
        wanted: list[str] = []
        for path in section.files_involved + sorted(state.files) + [
            f.path for f in self._scan.files if f.category is FileCategory.MANIFEST
        ]:
            if path not in wanted:
                wanted.append(path)

        budget = self._max_reference_tokens
        result: list[FileSnapshot] = []
        for path in wanted:
            file = self._scan.get(path)
            if file is None or not file.content_allowed:
                continue
            content = (self._repo_root / path).read_text(encoding="utf-8", errors="replace")
            cost = estimate_tokens(content)
            if cost > budget:
                continue
            budget -= cost
            result.append(FileSnapshot(path=path, content=content))
        return result

    def convergence_report(self, state: WorkspaceState) -> list[ConvergenceItem]:
        """Compare the tutorial's final files with the repository's code files."""
        repo_files = {
            f.path: f for f in self._scan.files
            if f.category in _CONVERGENCE_CATEGORIES and f.content_allowed
        }
        items: list[ConvergenceItem] = []
        for path in sorted(set(repo_files) | set(state.files)):
            if path not in state.files:
                items.append(ConvergenceItem(path=path, status=ConvergenceStatus.NOT_BUILT, similarity=0))
                continue
            if path not in repo_files:
                items.append(
                    ConvergenceItem(path=path, status=ConvergenceStatus.TUTORIAL_ONLY, similarity=0)
                )
                continue
            original = (self._repo_root / path).read_text(encoding="utf-8", errors="replace")
            ratio = file_similarity(original, state.files[path])
            if ratio >= 0.999:
                status = ConvergenceStatus.IDENTICAL
            elif ratio >= SIMILAR_THRESHOLD:
                status = ConvergenceStatus.SIMILAR
            else:
                status = ConvergenceStatus.DIFFERENT
            items.append(ConvergenceItem(path=path, status=status, similarity=round(ratio, 3)))
        return items


STOP_SERVER_NARRATION = "First I'll stop the server that's still running in the terminal."


def insert_server_stops(beats: list[BeatDraft], state: WorkspaceState) -> list[BeatDraft]:
    """The one script problem the AI keeps leaving in: starting a server (background command)
    while the previous one is still running. On camera that would type into the running server's
    terminal. The fix is always the same, so it is applied here instead of asked for again: a
    stop_process beat (Ctrl+C) goes in front, and the narration ("let's start the server again")
    still fits what the viewer sees."""
    state = state.copy()
    fixed: list[BeatDraft] = []
    for beat in beats:
        if beat.kind is BeatKind.RUN_COMMAND and beat.background and state.running:
            stop = BeatDraft(kind=BeatKind.STOP_PROCESS, narration=STOP_SERVER_NARRATION)
            fixed.append(stop)
            state.apply(stop)
        fixed.append(beat)
        state.apply(beat)
    return fixed


def fix_kept_section(beats: list[ScriptBeat], state: WorkspaceState) -> list[ScriptBeat]:
    """Same fix for a section kept from an earlier run. Inserted beats get a derived id (the
    existing ids stay, so narration audio recorded for them is still valid)."""
    fixed: list[ScriptBeat] = []
    replay = state.copy()
    for beat in beats:
        if beat.kind is BeatKind.RUN_COMMAND and beat.background and replay.running:
            stop = ScriptBeat(id=f"{beat.id}_stop", kind=BeatKind.STOP_PROCESS, narration=STOP_SERVER_NARRATION)
            fixed.append(stop)
            replay.apply(stop)
        fixed.append(beat)
        replay.apply(beat)
    return fixed


def project_commands(analysis: RepositoryAnalysis) -> set[str]:
    """Executables this repository documents - allowed in its tutorial (e.g. yt-dlp, ffmpeg)."""
    from tutorial_ai.security.command_validator import executables_in

    commands = [c.command for c in analysis.run_commands + analysis.test_commands]
    commands += analysis.setup_steps
    return {exe for command in commands for exe in executables_in(command)}


def file_similarity(a: str, b: str) -> float:
    """Line-based similarity that ignores trailing whitespace and blank-line differences."""
    def lines(text: str) -> list[str]:
        return [line.rstrip() for line in text.splitlines() if line.strip()]

    return difflib.SequenceMatcher(a=lines(a), b=lines(b), autojunk=False).ratio()


def summarize_analysis(analysis: RepositoryAnalysis) -> str:
    """Compact repository summary given to every script-writing call."""
    concepts = "; ".join(f"{c.name}: {c.explanation}" for c in analysis.important_concepts)
    return "\n".join([
        analysis.description,
        f"Language: {analysis.primary_language}. Frameworks: {', '.join(analysis.frameworks) or '-'}.",
        f"Run: {'; '.join(c.command for c in analysis.run_commands) or '-'}",
        f"Test: {'; '.join(c.command for c in analysis.test_commands) or '-'}",
        f"Application flow: {' -> '.join(analysis.application_flow) or '-'}",
        f"Key concepts: {concepts or '-'}",
        f"Repository structure:\n{analysis.project_structure}",
    ])  # fmt: skip
