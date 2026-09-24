"""The pipeline steps, shared by the CLI and the web API.

Each function does one step, persists its artifact in output/<project>/ and returns it. Errors
that the user can act on are raised as PipelineError with a readable message.
"""

from pathlib import Path

from pydantic import BaseModel

from tutorial_ai.config import Settings
from tutorial_ai.models.repository import RepositoryAnalysis
from tutorial_ai.models.scene import TutorialScenes
from tutorial_ai.models.script import TutorialScript
from tutorial_ai.models.tutorial import TutorialPlan, TutorialRequest
from tutorial_ai.providers.ai import (
    AIProvider,
    AIProviderError,
    ConfigurationError,
    create_ai_provider,
)
from tutorial_ai.repository.analyzer import RepositoryAnalyzer
from tutorial_ai.repository.clone import RepositoryError, resolve_source
from tutorial_ai.repository.context import RepositoryContext
from tutorial_ai.storage import ProjectPaths, load_model, save_model, write_text_atomic
from tutorial_ai.tutorial.planner import TutorialPlanner
from tutorial_ai.tutorial.scene_builder import DEFAULT_MAX_SCENE_SECONDS, build_scenes
from tutorial_ai.tutorial.script_writer import ScriptError, ScriptWriter
from tutorial_ai.utils.logging import get_logger

log = get_logger("pipeline")


class PipelineError(RuntimeError):
    pass


class AnalyzeResult(BaseModel):
    paths_root: Path
    context: RepositoryContext
    analysis: RepositoryAnalysis | None  # None for a dry run


def project_paths(settings: Settings, project: str) -> ProjectPaths:
    """A project name under the output directory, or a path to a project directory."""
    as_path = Path(project)
    if (as_path / "repository_analysis.json").exists():
        return ProjectPaths(root=as_path)
    return ProjectPaths.for_project(settings.output_directory, project)


def analyze(
    settings: Settings,
    source: str,
    *,
    name: str | None = None,
    output_directory: Path | None = None,
    refresh: bool = False,
    dry_run: bool = False,
    github_token: str | None = None,
    provider: AIProvider | None = None,
) -> AnalyzeResult:
    try:
        repo_source = resolve_source(
            source, settings.workspace_directory, refresh=refresh, github_token=github_token
        )
    except RepositoryError as exc:
        raise PipelineError(str(exc)) from exc

    paths = ProjectPaths.for_project(
        output_directory or settings.output_directory, name or repo_source.name
    ).ensure()
    log.info("Source: %s -> %s", repo_source.origin, repo_source.local_path)

    context = RepositoryAnalyzer(settings).prepare_context(repo_source)
    write_text_atomic(paths.repository_context, context.render())
    log.info("Wrote AI context to %s", paths.repository_context)
    if dry_run:
        return AnalyzeResult(paths_root=paths.root, context=context, analysis=None)

    try:
        analyzer = RepositoryAnalyzer(settings, provider or create_ai_provider(settings))
        analysis = analyzer.analyze(repo_source, context)
    except (ConfigurationError, AIProviderError) as exc:
        raise PipelineError(str(exc)) from exc
    save_model(analysis, paths.repository_analysis)
    log.info("Saved %s", paths.repository_analysis)
    return AnalyzeResult(paths_root=paths.root, context=context, analysis=analysis)


def require_analysis(paths: ProjectPaths, project: str) -> RepositoryAnalysis:
    if not paths.repository_analysis.exists():
        raise PipelineError(
            f"No repository_analysis.json in {paths.root}. "
            f"Run `tutorial-ai analyze <repo> --name {project}` first."
        )
    return load_model(paths.repository_analysis, RepositoryAnalysis)


def _require(path: Path, previous_step: str) -> None:
    if not path.exists():
        raise PipelineError(f"{path.name} not found in {path.parent}. Run `{previous_step}` first.")


def plan(
    settings: Settings,
    project: str,
    request: TutorialRequest,
    provider: AIProvider | None = None,
) -> tuple[TutorialPlan, list[str]]:
    paths = project_paths(settings, project).ensure()
    analysis = require_analysis(paths, project)
    try:
        planner = TutorialPlanner(provider or create_ai_provider(settings))
        tutorial_plan, warnings = planner.create_plan(analysis, request)
    except (ConfigurationError, AIProviderError) as exc:
        raise PipelineError(str(exc)) from exc
    save_model(tutorial_plan, paths.tutorial_plan)
    log.info("Saved %s", paths.tutorial_plan)
    return tutorial_plan, warnings


def script(
    settings: Settings,
    project: str,
    *,
    restart: bool = False,
    from_section: int | None = None,
    fix_from_rehearsal: bool = False,
    max_scene_seconds: float = DEFAULT_MAX_SCENE_SECONDS,
    provider: AIProvider | None = None,
) -> tuple[TutorialScript, TutorialScenes]:
    """`fix_from_rehearsal`: rewrite the first failing section (and everything after it) with the
    rehearsal's failures as feedback - the repair loop."""
    from tutorial_ai.tutorial.rehearsal import RehearsalReport

    paths = project_paths(settings, project).ensure()
    analysis = require_analysis(paths, project)
    _require(paths.tutorial_plan, "plan")
    tutorial_plan = load_model(paths.tutorial_plan, TutorialPlan)
    existing = None
    if paths.script.exists() and not restart:
        existing = load_model(paths.script, TutorialScript)
    feedback: dict[int, list[str]] = {}
    if fix_from_rehearsal:
        _require(paths.rehearsal, "rehearse")
        report = load_model(paths.rehearsal, RehearsalReport)
        feedback = report.feedback_by_section()
        if not feedback:
            raise PipelineError("The last rehearsal has no script failures to fix (environment "
                                "problems such as a busy port are not script problems).")
        from_section = min(feedback)
        log.info("Rewriting from section %d with rehearsal feedback for sections %s",
                 from_section, sorted(feedback))

    try:
        writer = ScriptWriter(
            provider or create_ai_provider(settings), analysis, tutorial_plan,
            max_file_bytes=settings.max_file_bytes, rehearsal_feedback=feedback,
        )
        def save_progress(partial: TutorialScript) -> None:
            # Keep scenes.json in step with script.json after every section, so a run that stops
            # halfway (quota, crash) never leaves scenes from an older script behind.
            save_model(partial, paths.script)
            save_model(build_scenes(partial, tutorial_plan, max_scene_seconds), paths.scenes)

        tutorial_script = writer.write(existing, from_section, on_section_done=save_progress)
    except (ConfigurationError, AIProviderError, ScriptError) as exc:
        raise PipelineError(
            f"{exc} (completed sections are saved in {paths.script}; run again to resume)"
        ) from exc

    save_model(tutorial_script, paths.script)
    scenes = build_and_save_scenes(settings, project, max_scene_seconds)
    return tutorial_script, scenes


def build_and_save_scenes(
    settings: Settings, project: str, max_scene_seconds: float = DEFAULT_MAX_SCENE_SECONDS
) -> TutorialScenes:
    paths = project_paths(settings, project)
    _require(paths.tutorial_plan, "plan")
    _require(paths.script, "script")
    result = build_scenes(
        load_model(paths.script, TutorialScript),
        load_model(paths.tutorial_plan, TutorialPlan),
        max_scene_seconds,
    )
    save_model(result, paths.scenes)
    log.info("Saved %s", paths.scenes)
    return result


def narrate(
    settings: Settings,
    project: str,
    *,
    tts_provider: str | None = None,
    voice: str | None = None,
    on_progress=None,
) -> "NarrationManifest":
    """Generate narration audio for every segment and record measured durations in scenes.json."""
    from tutorial_ai.audio.generator import NarrationGenerator, NarrationManifest
    from tutorial_ai.providers.tts import TTSError, create_tts_provider

    paths = project_paths(settings, project).ensure()
    _require(paths.scenes, "script")
    scenes = load_model(paths.scenes, TutorialScenes)
    existing = load_model(paths.narration, NarrationManifest) if paths.narration.exists() else None
    try:
        provider = create_tts_provider(settings, tts_provider, voice)
        manifest = NarrationGenerator(provider, paths.root).generate(scenes, existing, on_progress)
    except TTSError as exc:
        raise PipelineError(str(exc)) from exc
    save_model(manifest, paths.narration)
    save_model(scenes, paths.scenes)
    log.info("Saved %s", paths.narration)
    return manifest


def compile_timeline(settings: Settings, project: str, typing_speed: str = "normal") -> "Timeline":
    """Compile scene actions (typing only the diff) and build timeline.json from measured audio."""
    from tutorial_ai.automation.actions import ActionCompiler
    from tutorial_ai.automation.typing import HumanTypingEngine, TypingProfile, TypingSpeed
    from tutorial_ai.models.timeline import Timeline
    from tutorial_ai.timeline.builder import build_timeline
    from tutorial_ai.tutorial.workspace_state import WorkspaceState

    paths = project_paths(settings, project).ensure()
    _require(paths.scenes, "script")
    _require(paths.narration, "narrate")
    scenes = load_model(paths.scenes, TutorialScenes)
    compiler = ActionCompiler(HumanTypingEngine(TypingProfile.preset(TypingSpeed(typing_speed))))
    state = WorkspaceState()
    for scene in scenes.scenes:
        compiled = compiler.compile_scene(scene, state)
        scene.actions = compiled.actions
        for segment in scene.narration_segments:
            segment.linked_actions = [a.id for a in compiled.actions if a.beat_id == segment.beat_id]
    timeline = build_timeline(project, scenes)
    save_model(scenes, paths.scenes)
    save_model(timeline, paths.timeline)
    log.info("Timeline: %d scenes, %.1f minutes total", len(timeline.scenes),
             timeline.total_duration_seconds / 60)
    return timeline


def rehearse(settings: Settings, project: str, sections: list[int] | None = None, on_progress=None) -> "RehearsalReport":
    """Run every section's code in a throwaway workspace and save rehearsal.json."""
    from tutorial_ai.tutorial.rehearsal import Rehearsal, RehearsalReport

    paths = project_paths(settings, project).ensure()
    analysis = require_analysis(paths, project)
    _require(paths.script, "script")
    script = load_model(paths.script, TutorialScript)
    workspace = settings.workspace_directory.resolve() / paths.root.name / "rehearsal" / _folder_name(analysis)
    report = Rehearsal(analysis, workspace, on_progress).run(script, sections)
    save_model(report, paths.rehearsal)
    log.info("Saved %s", paths.rehearsal)
    return report


def _folder_name(analysis: RepositoryAnalysis) -> str:
    """The folder the viewer sees in VS Code (and the name `uv init`/`npm init` pick up)."""
    from tutorial_ai.utils.text import slugify

    return slugify(analysis.repository_name)


def _recording_session(settings: Settings, project: str, speed: str, audible: bool, on_progress, on_scene_done=None):
    from tutorial_ai.automation.typing import TypingSpeed
    from tutorial_ai.models.recording import RecordingsManifest
    from tutorial_ai.recording.session import RecordingSession

    paths = project_paths(settings, project).ensure()
    _require(paths.timeline, "timeline")
    scenes = load_model(paths.scenes, TutorialScenes)
    manifest = load_model(paths.recordings, RecordingsManifest) if paths.recordings.exists() else RecordingsManifest()
    workspace_root = settings.workspace_directory.resolve()
    analysis = require_analysis(paths, project)
    session = RecordingSession(
        paths, workspace_root / paths.root.name / "tutorial_build" / _folder_name(analysis), scenes, manifest,
        workspace_root=workspace_root, speed=TypingSpeed(speed), audible=audible,
        source=settings.recorder_source, on_progress=on_progress, on_scene_done=on_scene_done or (lambda m: save_model(m, paths.recordings)),
    )
    return session, paths


def record(settings: Settings, project: str, *, scenes: list[str] | None = None, from_scene: str | None = None,
           speed: str = "normal", audible: bool = False, on_progress=None) -> "RecordingsManifest":
    """Record scenes in VS Code (screen capture + narration muxed in) into scenes/scene_NNN.mp4."""
    from tutorial_ai.automation.macos import AutomationError
    from tutorial_ai.recording.recorder import RecorderError

    session, paths = _recording_session(settings, project, speed, audible, on_progress)
    try:
        manifest = session.record(scenes, from_scene)
    except (AutomationError, RecorderError) as exc:
        raise PipelineError(str(exc)) from exc
    save_model(manifest, paths.recordings)
    return manifest


def build(settings: Settings, project: str, on_progress=None) -> "RecordingsManifest":
    """Concatenate the recorded scenes into tutorial.mp4."""
    from tutorial_ai.recording.recorder import RecorderError

    session, paths = _recording_session(settings, project, "normal", False, on_progress)
    try:
        manifest = session.build()
    except (RecorderError, RuntimeError, ValueError) as exc:
        raise PipelineError(str(exc)) from exc
    save_model(manifest, paths.recordings)
    log.info("Final video: %s", paths.final_video)
    return manifest
