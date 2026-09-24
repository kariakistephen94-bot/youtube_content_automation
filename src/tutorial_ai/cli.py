"""tutorial-ai command line interface."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from tutorial_ai.config import Settings, get_settings
from tutorial_ai.automation.typing import TypingSpeed
from tutorial_ai.models.base import Difficulty
from tutorial_ai.models.repository import RepositoryAnalysis
from tutorial_ai.models.scene import TutorialScenes
from tutorial_ai.models.script import TutorialScript
from tutorial_ai.models.tutorial import TutorialPlan, TutorialRequest
from tutorial_ai import github as github_auth
from tutorial_ai import pipeline
from tutorial_ai.repository.analyzer import scan_summary
from tutorial_ai.storage import ProjectPaths, load_model
from tutorial_ai.tutorial.scene_builder import DEFAULT_MAX_SCENE_SECONDS
from tutorial_ai.utils.logging import add_file_logging, get_logger, setup_logging

app = typer.Typer(
    help="Turn a GitHub repository or local project into a programming tutorial video.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
log = get_logger("cli")


@app.callback()
def main() -> None:
    """AI Programming Tutorial Video Generator."""


_extra_secrets: list[str] = []


def _register_secret(value: str) -> None:
    _extra_secrets.append(value)
    setup_logging(get_settings().log_level, secrets=_secrets(get_settings()))


def _secrets(settings: Settings) -> list[str]:
    keys = [k.get_secret_value() for k in (settings.gemini_api_key, settings.openai_api_key) if k]
    return keys + _extra_secrets


@app.command()
def analyze(
    source: Annotated[str, typer.Argument(help="Public GitHub URL or local directory path.")],
    name: Annotated[
        str | None, typer.Option(help="Project name for output/<name>/ (default: repo name).")
    ] = None,
    output_dir: Annotated[
        Path | None, typer.Option(help="Override the output directory.")
    ] = None,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Delete and re-clone an existing GitHub clone.")
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Scan and build the AI context only; don't call the AI."),
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")] = False,
) -> None:
    """Analyze a repository and write output/<project>/repository_analysis.json."""
    settings = get_settings()
    setup_logging("DEBUG" if verbose else settings.log_level, secrets=_secrets(settings))
    token = github_auth.load_token()
    if token:
        _register_secret(token.access_token)
    try:
        result = pipeline.analyze(
            settings, source, name=name, output_directory=output_dir, refresh=refresh,
            dry_run=dry_run, github_token=token.access_token if token else None,
        )
    except pipeline.PipelineError as exc:
        _fail(str(exc))

    add_file_logging(result.paths_root / "logs" / "analyze.log", secrets=_secrets(settings))
    if result.analysis is None:
        console.print(f"\n[bold]Dry run[/bold] - no AI call made. Context: "
                      f"{result.paths_root / 'repository_context.md'}")
        console.print(scan_summary(result.context).model_dump())
        return
    _print_analysis(result.analysis, result.paths_root / "repository_analysis.json")


def _open_project(project: str, command: str, verbose: bool) -> tuple[Settings, ProjectPaths]:
    """Resolve PROJECT (a name under the output directory, or a project directory path)."""
    settings = get_settings()
    setup_logging("DEBUG" if verbose else settings.log_level, secrets=_secrets(settings))
    paths = pipeline.project_paths(settings, project)
    if not paths.repository_analysis.exists():
        _fail(
            f"No repository_analysis.json in {paths.root}. "
            f"Run `tutorial-ai analyze <repo> --name {project}` first."
        )
    paths.ensure()
    add_file_logging(paths.logs_dir / f"{command}.log", secrets=_secrets(settings))
    return settings, paths


def _require(path: Path, previous_command: str) -> None:
    if not path.exists():
        _fail(f"{path.name} not found in {path.parent}. Run `tutorial-ai {previous_command}` first.")


ProjectArg = Annotated[
    str, typer.Argument(help="Project name under the output directory (or its path).")
]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging.")]


@app.command()
def plan(
    project: ProjectArg,
    title: Annotated[str | None, typer.Option(help="Video title (default: AI suggestion).")] = None,
    audience: Annotated[Difficulty, typer.Option(help="Target audience.")] = Difficulty.BEGINNER,
    duration: Annotated[
        float, typer.Option(help="Target length in minutes.", min=1, max=180)
    ] = 20,
    notes: Annotated[
        str | None, typer.Option(help="Extra guidance, e.g. 'skip the tests section'.")
    ] = None,
    verbose: VerboseOpt = False,
) -> None:
    """Create output/<project>/tutorial_plan.json from the repository analysis."""
    settings, paths = _open_project(project, "plan", verbose)
    request = TutorialRequest(
        title=title, audience=audience, target_duration_minutes=duration, notes=notes
    )
    try:
        tutorial_plan, warnings = pipeline.plan(settings, project, request)
    except pipeline.PipelineError as exc:
        _fail(str(exc))

    table = Table(title=f"{tutorial_plan.title} (~{tutorial_plan.estimated_duration_seconds / 60:.1f} min)")
    table.add_column("#", justify="right")
    table.add_column("Section")
    table.add_column("Result")
    table.add_column("Min", justify="right")
    for section in tutorial_plan.sections:
        table.add_row(
            str(section.section_number), section.title, section.expected_result,
            f"{section.estimated_duration_seconds / 60:.1f}",
        )
    console.print(table)
    for warning in warnings:
        console.print(f"[yellow]warning[/yellow] {warning}")
    console.print(f"\n[green]Saved[/green] {paths.tutorial_plan}")


@app.command()
def script(
    project: ProjectArg,
    restart: Annotated[
        bool, typer.Option("--restart", help="Discard script.json and write every section again.")
    ] = False,
    from_section: Annotated[
        int | None, typer.Option(help="Rewrite this section and everything after it.", min=1)
    ] = None,
    fix_from_rehearsal: Annotated[
        bool, typer.Option("--fix-from-rehearsal", help="Rewrite the sections the last rehearsal failed, "
                                                         "giving the AI the failures as feedback.")
    ] = False,
    max_scene_seconds: Annotated[
        float, typer.Option(help="Split sections into scenes no longer than this.", min=20)
    ] = DEFAULT_MAX_SCENE_SECONDS,
    verbose: VerboseOpt = False,
) -> None:
    """Write script.json section by section (resumable), then build scenes.json."""
    settings, paths = _open_project(project, "script", verbose)
    try:
        tutorial_script, result = pipeline.script(
            settings, project, restart=restart, from_section=from_section,
            fix_from_rehearsal=fix_from_rehearsal, max_scene_seconds=max_scene_seconds,
        )
    except pipeline.PipelineError as exc:
        _fail(str(exc))
    _print_scenes(result)
    counts: dict[str, int] = {}
    for item in tutorial_script.convergence:
        counts[item.status.value] = counts.get(item.status.value, 0) + 1
    console.print(f"Convergence with the repository: {counts or 'no code files to compare'}")
    for warning in tutorial_script.warnings:
        console.print(f"[yellow]warning[/yellow] {warning}")
    console.print(f"[green]Saved[/green] {paths.script}")


@app.command()
def scenes(
    project: ProjectArg,
    max_scene_seconds: Annotated[
        float, typer.Option(help="Split sections into scenes no longer than this.", min=20)
    ] = DEFAULT_MAX_SCENE_SECONDS,
    verbose: VerboseOpt = False,
) -> None:
    """Rebuild scenes.json from script.json (no AI call)."""
    settings, _ = _open_project(project, "scenes", verbose)
    try:
        _print_scenes(pipeline.build_and_save_scenes(settings, project, max_scene_seconds))
    except pipeline.PipelineError as exc:
        _fail(str(exc))


def _print_scenes(result: TutorialScenes) -> None:
    table = Table(title=f"Scenes (~{result.estimated_duration_seconds / 60:.1f} min estimated)")
    table.add_column("Scene")
    table.add_column("Title")
    table.add_column("Beats", justify="right")
    table.add_column("Segments", justify="right")
    table.add_column("Sec", justify="right")
    for scene in result.scenes:
        table.add_row(
            scene.id, scene.title, str(len(scene.beats)), str(len(scene.narration_segments)),
            f"{scene.estimated_duration_seconds:.0f}",
        )
    console.print(table)


@app.command()
def show(
    project: ProjectArg,
    section: Annotated[
        int | None, typer.Option(help="Show only this script section.", min=1)
    ] = None,
    code: Annotated[
        bool, typer.Option("--code/--no-code", help="Show the code typed in each step.")
    ] = True,
) -> None:
    """Print a project's plan and script as a readable screenplay (no AI call)."""
    settings = get_settings()
    setup_logging(settings.log_level)
    as_path = Path(project)
    paths = (
        ProjectPaths(root=as_path) if as_path.is_dir()
        else ProjectPaths.for_project(settings.output_directory, project)
    )
    if not paths.repository_analysis.exists():
        _fail(f"Nothing to show in {paths.root}. Run `tutorial-ai analyze` first.")

    analysis = load_model(paths.repository_analysis, RepositoryAnalysis)
    if section is None:
        _print_analysis(analysis, paths.repository_analysis)
    if not paths.tutorial_plan.exists():
        console.print("\n[dim]No tutorial_plan.json yet - run `tutorial-ai plan`.[/dim]")
        return
    tutorial_plan = load_model(paths.tutorial_plan, TutorialPlan)
    if section is None:
        console.rule(f"[bold]Plan: {tutorial_plan.title}")
        console.print(tutorial_plan.summary)
        for s in tutorial_plan.sections:
            console.print(
                f"  [bold]{s.section_number}.[/bold] {s.title} "
                f"[dim]({s.estimated_duration_seconds / 60:.1f} min)[/dim] -> {s.expected_result}"
            )
    if not paths.script.exists():
        console.print("\n[dim]No script.json yet - run `tutorial-ai script`.[/dim]")
        return

    tutorial_script = load_model(paths.script, TutorialScript)
    _print_script(tutorial_script, section, code)


@app.command()
def narrate(
    project: ProjectArg,
    provider: Annotated[str | None, typer.Option(help="macos-say (default) or gemini-tts.")] = None,
    voice: Annotated[str | None, typer.Option(help="Voice name, e.g. Samantha or Kore.")] = None,
    verbose: VerboseOpt = False,
) -> None:
    """Generate narration audio for every segment (resumable) and measure real durations."""
    settings, paths = _open_project(project, "narrate", verbose)
    try:
        manifest = pipeline.narrate(settings, project, tts_provider=provider, voice=voice)
    except pipeline.PipelineError as exc:
        _fail(str(exc))
    console.print(
        f"{len(manifest.entries)} segments, {manifest.total_seconds / 60:.1f} min of audio "
        f"({manifest.provider}, voice {manifest.voice}) -> {paths.audio_dir}"
    )


@app.command()
def timeline(
    project: ProjectArg,
    speed: Annotated[TypingSpeed, typer.Option(help="Typing speed the actions are timed for.")] = TypingSpeed.NORMAL,
    verbose: VerboseOpt = False,
) -> None:
    """Compile scene actions and build timeline.json from the measured narration audio."""
    settings, paths = _open_project(project, "timeline", verbose)
    try:
        result = pipeline.compile_timeline(settings, project, speed.value)
    except pipeline.PipelineError as exc:
        _fail(str(exc))
    table = Table(title=f"Timeline (~{result.total_duration_seconds / 60:.1f} min)")
    table.add_column("Scene")
    table.add_column("Events", justify="right")
    table.add_column("Sec", justify="right")
    for scene in result.scenes:
        table.add_row(scene.scene_id, str(len(scene.events)), f"{scene.duration_seconds:.0f}")
    console.print(table)
    console.print(f"[green]Saved[/green] {paths.timeline}")


@app.command()
def rehearse(
    project: ProjectArg,
    section: Annotated[list[int] | None, typer.Option(help="Only check these sections.")] = None,
    verbose: VerboseOpt = False,
) -> None:
    """Run the script's code off camera (files, commands, servers, tests) and report failures."""
    settings, paths = _open_project(project, "rehearse", verbose)
    try:
        report = pipeline.rehearse(settings, project, section,
                                   on_progress=lambda m: console.print(escape(m)))
    except pipeline.PipelineError as exc:
        _fail(str(exc))
    for number, check in report.failures:
        where = f"section {number}" if number else "final tests"
        console.print(f"[red]FAILED[/red] {where} {check.beat_id} ({check.kind}): {escape(check.detail)}")
    console.print("[green]Rehearsal passed.[/green]" if report.ok else
                  f"[red]{len(report.failures)} failing check(s).[/red] Fix the script (e.g. `tutorial-ai script "
                  f"{project} --from-section N`) and rehearse again.")
    console.print(f"Report: {paths.rehearsal}")
    if not report.ok:
        raise typer.Exit(code=1)


@app.command()
def record(
    project: ProjectArg,
    scene: Annotated[list[str] | None, typer.Option(help="Only record these scene ids.")] = None,
    from_scene: Annotated[str | None, typer.Option(help="Record from this scene id onwards.")] = None,
    speed: Annotated[TypingSpeed, typer.Option(help="Typing speed.")] = TypingSpeed.NORMAL,
    audible: Annotated[bool, typer.Option("--audible", help="Also play the narration aloud while recording.")] = False,
    sandbox: Annotated[bool, typer.Option(
        "--sandbox", help="Record in the sandbox container (its own screen, VS Code and Chrome): "
                          "your Mac stays free. Live view: http://localhost:6080/vnc.html")] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Record scenes in VS Code: screen capture with the narration muxed in."""
    settings, paths = _open_project(project, "record", verbose)
    if sandbox:
        _record_in_sandbox(paths, scene, from_scene, speed.value)
        return
    import os
    import time as _time

    if not os.environ.get("TUTORIAL_AI_SANDBOX"):  # inside the sandbox nobody shares the screen
        console.print("[bold]Recording starts in 3 seconds.[/bold] Don't touch the keyboard or mouse; "
                      "clicking another window pauses the recording safely.")
        _time.sleep(3)
    try:
        manifest = pipeline.record(settings, project, scenes=scene, from_scene=from_scene,
                                   speed=speed.value, audible=audible,
                                   on_progress=lambda m: console.print(escape(m)))
    except pipeline.PipelineError as exc:
        _fail(str(exc))
    for result in manifest.results:
        colour = "green" if result.status.value == "SUCCESS" else "red"
        console.print(f"[{colour}]{result.status.value:8}[/{colour}] {result.scene_id} "
                      f"{result.video_path or ''} {escape(result.error or '')}")
    console.print(f"Recordings: {paths.scenes_dir}")


def _record_in_sandbox(paths: ProjectPaths, scenes: list[str] | None, from_scene: str | None, speed: str) -> None:
    from tutorial_ai import sandbox as sb

    try:
        sb.require_ready()
    except sb.SandboxError as exc:
        _fail(str(exc))
    console.print(f"Recording in the sandbox. Watch it live: {sb.LIVE_VIEW_URL.split('?')[0]}")
    try:
        code = sb.run(sb.record_args(paths.root.name, scenes, from_scene, speed), paths.root.parent,
                      on_line=lambda line: console.print(escape(line)))
    except sb.SandboxError as exc:
        _fail(str(exc))
    if code != 0:
        _fail(f"The sandbox recording stopped (exit code {code}).")


sandbox_app = typer.Typer(help="The recording sandbox: a container with its own screen, VS Code and Chrome.",
                          no_args_is_help=True)
app.add_typer(sandbox_app, name="sandbox")


@sandbox_app.command("status")
def sandbox_status() -> None:
    """Is Docker installed and running, and is the sandbox image built?"""
    from tutorial_ai import sandbox as sb

    state = sb.status()
    console.print(f"docker: {state.docker or 'not found'}\ndaemon running: {state.daemon_running}\n"
                  f"image built: {state.image_built}\ncontainer running: {state.running}\n{state.message}")


@sandbox_app.command("build")
def sandbox_build() -> None:
    """Build the sandbox image (once; again after dependency changes)."""
    from tutorial_ai import sandbox as sb

    setup_logging(get_settings().log_level)
    try:
        sb.build_image(on_line=lambda line: console.print(escape(line)))
    except sb.SandboxError as exc:
        _fail(str(exc))


@sandbox_app.command("stop")
def sandbox_stop() -> None:
    """Stop a running sandbox (ends its recording)."""
    from tutorial_ai import sandbox as sb

    console.print("Stopped the sandbox." if sb.stop() else "No sandbox was running.")


@sandbox_app.command("shell", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def sandbox_shell(ctx: typer.Context) -> None:
    """Open a shell (or run `-c '<command>'`) in a fresh sandbox, with its desktop running."""
    import os

    from tutorial_ai import sandbox as sb

    try:
        docker = sb.require_ready()
    except sb.SandboxError as exc:
        _fail(str(exc))
    sb.stop()
    command = sb.run_command(docker, ["shell", *ctx.args], get_settings().output_directory)
    command.insert(2, "-it" if os.isatty(0) else "-i")
    raise typer.Exit(subprocess_call(command))


def subprocess_call(command: list[str]) -> int:
    import subprocess

    return subprocess.call(command)


@app.command()
def build(project: ProjectArg, verbose: VerboseOpt = False) -> None:
    """Join the recorded scenes into output/<project>/tutorial.mp4."""
    settings, paths = _open_project(project, "build", verbose)
    try:
        manifest = pipeline.build(settings, project, on_progress=lambda m: console.print(escape(m)))
    except pipeline.PipelineError as exc:
        _fail(str(exc))
    console.print(f"[green]Final video:[/green] {paths.final_video} "
                  f"({(manifest.final_duration_seconds or 0) / 60:.1f} min)")


@app.command("vscode-test")
def vscode_test(
    speed: Annotated[TypingSpeed, typer.Option(help="Typing speed preset.")] = TypingSpeed.NORMAL,
    text: Annotated[str, typer.Option(help="Code to type.")] = 'print("Hello from AI")',
    sandbox: Annotated[bool, typer.Option("--sandbox", help="Run it in the sandbox container, not on this Mac.")] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Phase 5 check: open VS Code, create main.py, type code, save, run it in the terminal."""
    from tutorial_ai.automation import macos
    from tutorial_ai.automation.smoke_test import run_vscode_smoke_test

    settings = get_settings()
    setup_logging("DEBUG" if verbose else settings.log_level)
    if sandbox:
        from tutorial_ai import sandbox as sb

        try:
            code = sb.run(["vscode-test", "--speed", speed.value, "--text", text], settings.output_directory,
                          on_line=lambda line: console.print(escape(line)))
        except sb.SandboxError as exc:
            _fail(str(exc))
        raise typer.Exit(code=code)
    try:
        result = run_vscode_smoke_test(settings, speed, text, step=console.print)
    except macos.AutomationError as exc:
        console.print(f"[red]Stopped:[/red] {exc}")
        raise typer.Exit(code=1)

    console.print(f"   main.py on disk: {result.file_content.strip()!r}")
    console.print(f"   {result.command} printed: {result.command_output!r} (exit {result.exit_code})")
    if result.success:
        console.print("[green]VS Code automation works.[/green]")
    else:
        console.print("[red]Something did not match - see above.[/red]")
        raise typer.Exit(code=1)


@app.command()
def serve(
    port: Annotated[int, typer.Option(help="Port for the local API.")] = 8765,
    verbose: VerboseOpt = False,
) -> None:
    """Start the local web API used by the frontend (web/). Listens on 127.0.0.1 only."""
    import uvicorn

    from tutorial_ai.api.app import create_app

    settings = get_settings()
    setup_logging("DEBUG" if verbose else settings.log_level, secrets=_secrets(settings))
    console.print(f"tutorial-ai API on http://127.0.0.1:{port} (docs: /api/docs)")
    uvicorn.run(create_app(settings), host="127.0.0.1", port=port, log_level="warning")


_BEAT_STYLE = {
    "talk": "cyan", "write_code": "green", "run_command": "yellow", "highlight": "magenta",
    "open_browser": "blue", "try_endpoint": "blue", "stop_process": "red", "delete_file": "red",
}  # fmt: skip


def _print_script(tutorial_script: TutorialScript, only_section: int | None, code: bool) -> None:
    from rich.syntax import Syntax

    from tutorial_ai.tutorial.durations import typed_text
    from tutorial_ai.tutorial.workspace_state import WorkspaceState

    state = WorkspaceState()
    for script_section in tutorial_script.sections:
        visible = only_section in (None, script_section.section_number)
        if visible:
            console.rule(f"[bold]Section {script_section.section_number}: {script_section.title}")
        for beat in script_section.beats:
            before = state.files.get(beat.file or "", "")
            state.apply(beat)
            if not visible:
                continue
            kind = beat.kind.value
            target = beat.file or beat.command or beat.url or ""
            if kind == "try_endpoint":
                target = f"{(beat.method or '').upper()} {beat.path}  in {beat.url}"
            if beat.highlight:
                target += f"  -> {beat.highlight!r}"
            if beat.background:
                target += "  (background)"
            console.print(f"[{_BEAT_STYLE[kind]}]{beat.id} {kind.upper()}[/] {target}")
            if beat.narration:
                console.print(f'   [italic]"{beat.narration}"[/italic]')
            if code and beat.code_after is not None:
                typed = typed_text(before, beat.code_after).strip("\n")
                if typed:
                    lexer = Syntax.guess_lexer(beat.file or "", code=beat.code_after)
                    console.print(Syntax(typed, lexer, theme="ansi_dark", line_numbers=False))
        if visible:
            console.print(f"[dim]State after section: {script_section.state_summary}[/dim]")

    if only_section is None:
        console.rule("[bold]Summary")
        console.print(f"Sections written: {len(tutorial_script.sections)}")
        counts: dict[str, int] = {}
        for item in tutorial_script.convergence:
            counts[item.status.value] = counts.get(item.status.value, 0) + 1
        console.print(f"Convergence with the repository: {counts or '-'}")
        for warning in tutorial_script.warnings:
            console.print(f"[yellow]warning[/yellow] {warning}")


def _print_analysis(analysis: RepositoryAnalysis, path: Path) -> None:
    table = Table(title=f"Repository analysis: {analysis.repository_name}", show_lines=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Language", analysis.primary_language)
    table.add_row("Frameworks", ", ".join(analysis.frameworks) or "-")
    table.add_row("Difficulty", analysis.difficulty.value)
    table.add_row("Entry points", ", ".join(e.path for e in analysis.entry_points) or "-")
    table.add_row("Important files", str(len(analysis.important_files)))
    table.add_row("Dependencies", str(len(analysis.dependencies)))
    table.add_row("Run", "\n".join(c.command for c in analysis.run_commands) or "-")
    table.add_row("Teaching steps", "\n".join(
        f"{i}. {s.title}" for i, s in enumerate(analysis.potential_teaching_order, 1)
    ) or "-")
    table.add_row("Context sent", f"~{analysis.scan_summary.estimated_context_tokens:,} tokens, "
                  f"{analysis.scan_summary.files_with_content_sent} files")
    table.add_row("Warnings", str(len(analysis.validation_warnings)))
    console.print(table)
    console.print(f"\n[green]Saved[/green] {path}")


def _fail(message: str) -> None:
    log.error(message)
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
