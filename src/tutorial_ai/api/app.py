"""Local web API for the tutorial-ai frontend (Next.js in web/).

Binds to 127.0.0.1 only. State-changing requests must carry the `X-Tutorial-AI: 1` header: a
normal website cannot add custom headers to cross-origin requests without a CORS preflight, and
this API never answers preflights, so other sites open in your browser cannot trigger jobs.
"""

import re
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tutorial_ai import github, pipeline, sandbox
from tutorial_ai.providers.ai import AIProviderError, create_ai_provider
from tutorial_ai.api.jobs import Job, JobBusyError, JobManager
from tutorial_ai.audio.generator import NarrationManifest
from tutorial_ai.automation.typing import TypingSpeed
from tutorial_ai.config import Settings, ai_model, get_settings, with_ai_model
from tutorial_ai.models.base import Difficulty
from tutorial_ai.models.repository import RepositoryAnalysis
from tutorial_ai.models.scene import TutorialScenes
from tutorial_ai.models.script import TutorialScript
from tutorial_ai.models.recording import RecordingsManifest
from tutorial_ai.models.timeline import Timeline
from tutorial_ai.tutorial.rehearsal import RehearsalReport
from tutorial_ai.models.tutorial import TutorialPlan, TutorialRequest
from tutorial_ai.storage import ProjectPaths, load_model
from tutorial_ai.utils.logging import get_logger

log = get_logger("api")
CSRF_HEADER = "x-tutorial-ai"
_PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")


# --- request / response models -------------------------------------------------------------


class ModelOverride(BaseModel):
    model: str | None = Field(
        default=None, description="Use this AI model for the job instead of the configured one."
    )


class AnalyzeJobRequest(ModelOverride):
    source: str = Field(min_length=1, description="GitHub URL or local directory path.")
    name: str | None = None
    refresh: bool = False


class PlanJobRequest(ModelOverride):
    project: str
    title: str | None = None
    audience: Difficulty = Difficulty.BEGINNER
    duration_minutes: float = Field(default=10, gt=0, le=180)
    notes: str | None = None


class ScriptJobRequest(ModelOverride):
    project: str
    restart: bool = False
    from_section: int | None = Field(default=None, ge=1)
    fix_from_rehearsal: bool = False


class NarrateJobRequest(BaseModel):
    project: str
    provider: str | None = None
    voice: str | None = None


class TimelineJobRequest(BaseModel):
    project: str
    speed: TypingSpeed = TypingSpeed.NORMAL


class RehearseJobRequest(BaseModel):
    project: str
    sections: list[int] | None = None


class RecordJobRequest(BaseModel):
    project: str
    scenes: list[str] | None = None
    from_scene: str | None = None
    speed: TypingSpeed = TypingSpeed.NORMAL
    audible: bool = False
    sandbox: bool = Field(default=False, description="Record in the sandbox container, not on this Mac's screen")


class BuildJobRequest(BaseModel):
    project: str


class VSCodeTestRequest(BaseModel):
    speed: TypingSpeed = TypingSpeed.NORMAL
    sandbox: bool = Field(default=False, description="Run the test in the sandbox container")


class ProjectSummary(BaseModel):
    name: str
    repository_name: str | None
    source: str | None
    has_analysis: bool
    has_plan: bool
    plan_title: str | None
    plan_sections: int
    script_sections: int
    scenes: int
    updated_at: datetime | None


class ProjectDetail(BaseModel):
    summary: ProjectSummary
    analysis: RepositoryAnalysis | None
    plan: TutorialPlan | None
    script: TutorialScript | None
    scenes: TutorialScenes | None
    typed_code: dict[str, str] = Field(
        default_factory=dict, description="beat id -> the code actually typed in that beat"
    )
    narration: NarrationManifest | None = None
    timeline: Timeline | None = None
    rehearsal: RehearsalReport | None = None
    recordings: RecordingsManifest | None = None


class GitHubStatus(BaseModel):
    configured: bool
    connected: bool
    user: github.GitHubUser | None = None
    error: str | None = None


class DeviceStart(BaseModel):
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


class DevicePoll(BaseModel):
    status: str
    interval: int | None = None
    user: github.GitHubUser | None = None


# --- app ------------------------------------------------------------------------------------


def _record_in_sandbox(paths, body: "RecordJobRequest", step) -> dict:
    """Run the recording in the sandbox container and report it like a local one."""
    scenes = load_model(paths.scenes, TutorialScenes).scenes
    ids = [s.id for s in scenes]
    wanted = body.scenes or ids[ids.index(body.from_scene) if body.from_scene in ids else 0:]

    def on_line(line: str) -> None:
        # "Recording scene_004: ..." -> "Recording scene 2/6: scene_004 ..." so the job shows progress.
        match = re.match(r"Recording (scene_\w+): (.*)", line)
        if match and match.group(1) in wanted:
            line = f"Recording scene {wanted.index(match.group(1)) + 1}/{len(wanted)}: {match.group(1)} {match.group(2)}"
        step(line)

    step("Recording in the sandbox: your Mac's keyboard and screen stay free.")
    code = sandbox.run(sandbox.record_args(paths.root.name, body.scenes, body.from_scene, body.speed.value),
                       paths.root.parent, on_line=on_line)
    manifest = load_model(paths.recordings, RecordingsManifest) if paths.recordings.exists() else None
    results = [r for r in (manifest.results if manifest else []) if r.scene_id in wanted]
    failed = [r for r in results if r.status.value != "SUCCESS"]
    if failed:
        raise RuntimeError(f"{failed[0].scene_id}: {failed[0].error}")
    if code != 0:
        raise RuntimeError(f"The sandbox stopped with exit code {code}; see the log above.")
    step(f"Recorded {len(results)} scene(s) in the sandbox.")
    return {"project": paths.root.name}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="tutorial-ai", docs_url="/api/docs", openapi_url="/api/openapi.json")
    device_codes: dict[str, str] = {}  # user_code -> device_code (kept server-side)

    def secrets() -> list[str]:
        values = [k.get_secret_value() for k in (settings.gemini_api_key, settings.openai_api_key) if k]
        token = github.load_token(settings.config_directory)
        return values + ([token.access_token] if token else [])

    jobs = JobManager(secrets)
    app.state.jobs = jobs

    @app.middleware("http")
    async def require_csrf_header(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
            return JSONResponse({"detail": f"Missing {CSRF_HEADER} header"}, status_code=403)
        return await call_next(request)

    def github_token() -> str | None:
        token = github.load_token(settings.config_directory)
        return token.access_token if token else None

    def require_client_id() -> str:
        if not settings.github_client_id:
            raise HTTPException(
                400, "GITHUB_CLIENT_ID is not set. Register a GitHub OAuth App with Device Flow "
                     "enabled and add its Client ID to .env.local (see README).",
            )
        return settings.github_client_id

    def provider_for(model: str | None):
        """A provider for an explicitly requested model; None means "use the configured one"."""
        if not model or model == ai_model(settings):
            return None
        return create_ai_provider(with_ai_model(settings, model))

    def submit(kind: str, work, project: str | None = None) -> Job:
        try:
            return jobs.submit(kind, work, project)
        except JobBusyError as exc:
            raise HTTPException(409, str(exc)) from exc

    # --- health / github ------------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        configured = settings.openai_api_key if settings.ai_provider == "openai" else settings.gemini_api_key
        return {"ok": True, "ai_provider": settings.ai_provider, "ai_model": ai_model(settings),
                "ai_configured": configured is not None, "tts_provider": settings.tts_provider,
                # kept for older clients
                "gemini_model": ai_model(settings), "gemini_configured": configured is not None}

    @app.get("/api/models")
    def models() -> dict[str, Any]:
        """Text models this API key can actually use (quotas are counted per model)."""
        try:
            if settings.ai_provider == "openai":
                if settings.openai_api_key is None:
                    raise HTTPException(400, "OPENAI_API_KEY is not set.")
                from tutorial_ai.providers.ai.openai_provider import list_openai_models

                available = list_openai_models(settings.openai_api_key.get_secret_value())
            else:
                if settings.gemini_api_key is None:
                    raise HTTPException(400, "GEMINI_API_KEY is not set.")
                from tutorial_ai.providers.ai.gemini import list_text_models

                available = list_text_models(settings.gemini_api_key.get_secret_value())
        except AIProviderError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"provider": settings.ai_provider, "current": ai_model(settings), "available": available}

    @app.get("/api/github/status")
    def github_status() -> GitHubStatus:
        token = github.load_token(settings.config_directory)
        status = GitHubStatus(configured=bool(settings.github_client_id), connected=token is not None)
        if token:
            try:
                status.user = github.GitHubClient(token.access_token).get_user()
            except github.GitHubError as exc:
                status.error = str(exc)
        return status

    @app.post("/api/github/device/start")
    def github_device_start(client_id: str = Depends(require_client_id)) -> DeviceStart:
        try:
            code = github.start_device_flow(client_id)
        except github.GitHubError as exc:
            raise HTTPException(502, str(exc)) from exc
        device_codes.clear()
        device_codes[code.user_code] = code.device_code
        return DeviceStart(user_code=code.user_code, verification_uri=code.verification_uri,
                           expires_in=code.expires_in, interval=code.interval)

    @app.post("/api/github/device/poll")
    def github_device_poll(
        body: dict[str, str], client_id: str = Depends(require_client_id)
    ) -> DevicePoll:
        device_code = device_codes.get(body.get("user_code", ""))
        if device_code is None:
            raise HTTPException(400, "Unknown or expired sign-in code. Start again.")
        try:
            result = github.poll_device_flow(client_id, device_code)
        except github.GitHubError as exc:
            raise HTTPException(502, str(exc)) from exc
        if result.status != "authorized" or result.token is None:
            if result.status in ("expired", "denied"):
                device_codes.clear()
            return DevicePoll(status=result.status, interval=result.interval)
        user = github.GitHubClient(result.token.access_token).get_user()
        result.token.login = user.login
        github.save_token(result.token, settings.config_directory)
        device_codes.clear()
        return DevicePoll(status="authorized", user=user)

    @app.post("/api/github/disconnect")
    def github_disconnect() -> dict[str, bool]:
        github.delete_token(settings.config_directory)
        return {"ok": True}

    @app.get("/api/github/repos")
    def github_repos() -> list[github.GitHubRepoInfo]:
        token = github_token()
        if token is None:
            raise HTTPException(401, "GitHub is not connected.")
        try:
            return github.GitHubClient(token).list_repos()
        except github.GitHubError as exc:
            raise HTTPException(502, str(exc)) from exc

    # --- projects -------------------------------------------------------------------------

    def paths_for(name: str) -> ProjectPaths:
        if not _PROJECT_NAME.match(name):
            raise HTTPException(400, f"Invalid project name {name!r}")
        return ProjectPaths.for_project(settings.output_directory, name)

    @app.get("/api/projects")
    def list_projects() -> list[ProjectSummary]:
        root = settings.output_directory
        if not root.exists():
            return []
        summaries = [
            _summary(ProjectPaths(root=d)) for d in root.iterdir()
            if d.is_dir() and _PROJECT_NAME.match(d.name)
        ]
        return sorted(summaries, key=lambda s: s.updated_at or datetime.min.replace(tzinfo=UTC),
                      reverse=True)

    @app.get("/api/projects/{name}")
    def get_project(name: str) -> ProjectDetail:
        paths = paths_for(name)
        if not paths.root.exists():
            raise HTTPException(404, f"No project named {name!r}")
        tutorial_script = _load(paths.script, TutorialScript)
        return ProjectDetail(
            summary=_summary(paths),
            analysis=_load(paths.repository_analysis, RepositoryAnalysis),
            plan=_load(paths.tutorial_plan, TutorialPlan),
            script=tutorial_script,
            scenes=_load(paths.scenes, TutorialScenes),
            typed_code=_typed_code(tutorial_script) if tutorial_script else {},
            narration=_load(paths.narration, NarrationManifest),
            timeline=_load(paths.timeline, Timeline),
            rehearsal=_load(paths.rehearsal, RehearsalReport),
            recordings=_load(paths.recordings, RecordingsManifest),
        )

    # --- jobs -----------------------------------------------------------------------------

    @app.get("/api/jobs")
    def list_jobs() -> list[Job]:
        return [j.model_copy(update={"logs": j.logs[-5:]}) for j in jobs.list()]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, since: int = 0) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "No such job")
        data = job.model_dump(mode="json", exclude={"logs"})
        if job.status.value == "running" and job.progress is None:
            data["progress"] = None
        data["logs"] = job.logs[since:]
        data["log_count"] = len(job.logs)
        return data

    @app.post("/api/jobs/analyze")
    def start_analyze(body: AnalyzeJobRequest) -> Job:
        def work(step):
            result = pipeline.analyze(
                settings, body.source, name=body.name, refresh=body.refresh,
                github_token=github_token(), provider=provider_for(body.model),
            )
            step(f"Analysis saved for project {result.paths_root.name}")
            return {"project": result.paths_root.name}

        return submit("analyze", work, body.name)

    @app.post("/api/jobs/plan")
    def start_plan(body: PlanJobRequest) -> Job:
        paths_for(body.project)
        request = TutorialRequest(
            title=body.title or None, audience=body.audience,
            target_duration_minutes=body.duration_minutes, notes=body.notes or None,
        )

        def work(step):
            tutorial_plan, warnings = pipeline.plan(
                settings, body.project, request, provider=provider_for(body.model)
            )
            for warning in warnings:
                step(f"WARNING {warning}")
            step(f"Plan saved: {len(tutorial_plan.sections)} sections")
            return {"project": body.project}

        return submit("plan", work, body.project)

    @app.post("/api/jobs/script")
    def start_script(body: ScriptJobRequest) -> Job:
        paths_for(body.project)

        def work(step):
            tutorial_script, scenes = pipeline.script(
                settings, body.project, restart=body.restart, from_section=body.from_section,
                fix_from_rehearsal=body.fix_from_rehearsal, provider=provider_for(body.model),
            )
            step(f"Script saved: {len(tutorial_script.sections)} sections, {len(scenes.scenes)} scenes")
            return {"project": body.project}

        return submit("script", work, body.project)

    @app.get("/api/projects/{name}/files/{path:path}")
    def project_file(name: str, path: str):
        """Serve generated media (audio, recordings) from the project directory."""
        from fastapi.responses import FileResponse

        root = paths_for(name).root.resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(404, "No such file")
        if target.suffix not in {".wav", ".mp3", ".mp4", ".json", ".md", ".log"}:
            raise HTTPException(403, "File type not served")
        return FileResponse(target)

    @app.post("/api/jobs/narrate")
    def start_narrate(body: NarrateJobRequest) -> Job:
        paths_for(body.project)

        def work(step):
            manifest = pipeline.narrate(
                settings, body.project, tts_provider=body.provider, voice=body.voice, on_progress=step
            )
            step(f"Narration saved: {len(manifest.entries)} segments, "
                 f"{manifest.total_seconds / 60:.1f} min of audio")
            return {"project": body.project}

        return submit("narrate", work, body.project)

    @app.post("/api/jobs/timeline")
    def start_timeline(body: TimelineJobRequest) -> Job:
        paths_for(body.project)

        def work(step):
            result = pipeline.compile_timeline(settings, body.project, body.speed.value)
            step(f"Timeline saved: {len(result.scenes)} scenes, {result.total_duration_seconds / 60:.1f} min")
            return {"project": body.project}

        return submit("timeline", work, body.project)

    @app.post("/api/jobs/rehearse")
    def start_rehearse(body: RehearseJobRequest) -> Job:
        paths_for(body.project)

        def work(step):
            report = pipeline.rehearse(settings, body.project, body.sections, on_progress=step)
            step("Rehearsal passed." if report.ok else f"Rehearsal found {len(report.failures)} failing check(s).")
            return {"project": body.project, "ok": report.ok}

        return submit("rehearse", work, body.project)

    @app.get("/api/sandbox")
    def sandbox_status() -> sandbox.SandboxStatus:
        return sandbox.status()

    @app.post("/api/sandbox/stop")
    def sandbox_stop() -> dict:
        return {"stopped": sandbox.stop()}

    @app.post("/api/jobs/sandbox-build")
    def start_sandbox_build() -> Job:
        def work(step):
            sandbox.build_image(on_line=step)
            return {}

        return submit("sandbox-build", work)

    @app.post("/api/jobs/record")
    def start_record(body: RecordJobRequest) -> Job:
        paths = paths_for(body.project)
        if body.sandbox:
            return submit("record", lambda step: _record_in_sandbox(paths, body, step), body.project)

        def work(step):
            step("Recording starts in 3 seconds. Hands off the keyboard and mouse.")
            import time as _time
            _time.sleep(3)
            manifest = pipeline.record(
                settings, body.project, scenes=body.scenes, from_scene=body.from_scene,
                speed=body.speed.value, audible=body.audible, on_progress=step,
            )
            failed = [r for r in manifest.results if r.status.value != "SUCCESS"]
            if failed:
                raise RuntimeError(f"{failed[0].scene_id}: {failed[0].error}")
            step(f"Recorded {len(manifest.results)} scene(s).")
            return {"project": body.project}

        return submit("record", work, body.project)

    @app.post("/api/jobs/build")
    def start_build(body: BuildJobRequest) -> Job:
        paths_for(body.project)

        def work(step):
            manifest = pipeline.build(settings, body.project, on_progress=step)
            step(f"tutorial.mp4 ready: {(manifest.final_duration_seconds or 0) / 60:.1f} min")
            return {"project": body.project}

        return submit("build", work, body.project)

    @app.post("/api/jobs/vscode-test")
    def start_vscode_test(body: VSCodeTestRequest) -> Job:
        from tutorial_ai.automation.smoke_test import run_vscode_smoke_test

        if body.sandbox:
            def in_sandbox(step):
                step("Running the VS Code test in the sandbox: your Mac's keyboard and screen stay free.")
                code = sandbox.run(["vscode-test", "--speed", body.speed.value],
                                   settings.output_directory, on_line=step)
                if code != 0:
                    raise RuntimeError(f"The VS Code test failed in the sandbox (exit code {code}); see the log above.")
                return {}

            return submit("vscode-test", in_sandbox)

        def work(step):
            result = run_vscode_smoke_test(settings, body.speed, step=step)
            step("VS Code automation works." if result.success else "Result did not match.")
            if not result.success:
                raise RuntimeError(f"Output was {result.command_output!r}")
            return result.model_dump()

        return submit("vscode-test", work)

    return app


def _load[T: BaseModel](path, model: type[T]) -> T | None:
    return load_model(path, model) if path.exists() else None


def _typed_code(tutorial_script: TutorialScript) -> dict[str, str]:
    from tutorial_ai.tutorial.durations import typed_text
    from tutorial_ai.tutorial.workspace_state import WorkspaceState

    state = WorkspaceState()
    typed: dict[str, str] = {}
    for beat in tutorial_script.beats:
        if beat.code_after is not None and beat.file:
            typed[beat.id] = typed_text(state.files.get(beat.file, ""), beat.code_after)
        state.apply(beat)
    return typed


def _summary(paths: ProjectPaths) -> ProjectSummary:
    analysis = _load(paths.repository_analysis, RepositoryAnalysis)
    tutorial_plan = _load(paths.tutorial_plan, TutorialPlan)
    tutorial_script = _load(paths.script, TutorialScript)
    scenes = _load(paths.scenes, TutorialScenes)
    files = [p for p in (paths.repository_analysis, paths.tutorial_plan, paths.script) if p.exists()]
    updated = max((datetime.fromtimestamp(p.stat().st_mtime, UTC) for p in files), default=None)
    return ProjectSummary(
        name=paths.root.name,
        repository_name=analysis.repository_name if analysis else None,
        source=analysis.source.origin if analysis else None,
        has_analysis=analysis is not None,
        has_plan=tutorial_plan is not None,
        plan_title=tutorial_plan.title if tutorial_plan else None,
        plan_sections=len(tutorial_plan.sections) if tutorial_plan else 0,
        script_sections=len(tutorial_script.sections) if tutorial_script else 0,
        scenes=len(scenes.scenes) if scenes else 0,
        updated_at=updated,
    )
