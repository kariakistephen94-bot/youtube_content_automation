import shutil

import pytest
from conftest import EXAMPLE_REPO, FakeProvider, make_insights, make_plan, make_sections, write

from tutorial_ai.models.repository import RepositoryAnalysis
from tutorial_ai.models.script import ConvergenceStatus, ScriptSectionDraft
from tutorial_ai.models.tutorial import TutorialRequest
from tutorial_ai.providers.ai import prompts
from tutorial_ai.repository.analyzer import RepositoryAnalyzer
from tutorial_ai.repository.clone import resolve_source
from tutorial_ai.tutorial.planner import TutorialPlanner
from tutorial_ai.tutorial.script_writer import ScriptError, ScriptWriter, file_similarity


@pytest.fixture
def analysis(settings) -> RepositoryAnalysis:
    source = resolve_source(str(EXAMPLE_REPO), settings.workspace_directory)
    return RepositoryAnalyzer(settings, FakeProvider()).analyze(source)


# --- planner ------------------------------------------------------------------------------------


def test_planner_applies_user_choices(analysis):
    provider = FakeProvider(plan=make_plan(audience="advanced"))
    request = TutorialRequest(title="My Title", audience="beginner", target_duration_minutes=6)
    plan, warnings = TutorialPlanner(provider).create_plan(analysis, request)
    assert plan.title == "My Title"
    assert plan.audience.value == "beginner"
    assert plan.estimated_duration_seconds == 360  # recomputed from sections
    assert warnings == []
    assert provider.plan_requests == [request]


def test_planner_warns_about_duration_and_unknown_files(analysis):
    plan = make_plan()
    plan.sections[1].files_involved = ["app/main.py", "app/invented.py"]
    request = TutorialRequest(target_duration_minutes=25)
    _, warnings = TutorialPlanner(FakeProvider(plan=plan)).create_plan(analysis, request)
    assert any("differs from the requested 25 min" in w for w in warnings)
    assert any("app/invented.py" in w for w in warnings)
    assert not any("'app/main.py'" in w for w in warnings)


# --- script writer ------------------------------------------------------------------------------


def test_writes_all_sections_in_order_with_growing_workspace(analysis):
    provider = FakeProvider()
    saved = []
    script = ScriptWriter(provider, analysis, make_plan()).write(
        on_section_done=lambda s: saved.append(len(s.sections))
    )
    assert [s.section_number for s in script.sections] == [1, 2, 3]
    assert saved == [1, 2, 3]
    assert script.sections[1].beats[0].id == "s02_b01"
    assert script.warnings == []

    requests = provider.section_requests
    assert requests[0].workspace_files == []
    assert [f.path for f in requests[2].workspace_files] == ["app/__init__.py", "app/main.py"]
    assert requests[2].running_processes == ["uv run fastapi dev app/main.py"]
    assert requests[2].previous_state_summaries[-1].startswith("app/main.py has")


def test_reference_files_come_from_the_repository_without_secrets(tmp_path, settings):
    repo = tmp_path / "repo"
    shutil.copytree(EXAMPLE_REPO, repo)
    write(repo, ".env", "UPLOAD_SECRET=do-not-leak\n")
    source = resolve_source(str(repo), settings.workspace_directory)
    analysis = RepositoryAnalyzer(settings, FakeProvider()).analyze(source)
    plan = make_plan()
    plan.sections[1].files_involved = ["app/main.py", ".env"]

    provider = FakeProvider(plan=plan)
    ScriptWriter(provider, analysis, plan).write()
    refs = {f.path: f.content for f in provider.section_requests[1].reference_files}
    assert "app/main.py" in refs  # files_involved
    assert "pyproject.toml" in refs  # manifests always included
    assert ".env" not in refs
    assert not any("do-not-leak" in c for c in refs.values())


def test_reference_budget_is_respected(analysis):
    provider = FakeProvider()
    ScriptWriter(provider, analysis, make_plan(), max_reference_tokens=50).write()
    for request in provider.section_requests:
        assert sum(len(f.content) for f in request.reference_files) <= 50 * 4


def test_problems_are_sent_back_for_repair(analysis):
    sections = make_sections()
    broken = ScriptSectionDraft.model_validate({
        "beats": [{"kind": "stop_process", "narration": "Stop the server."}],
        "state_summary": "s",
    })
    sections[1] = [broken, sections[1]]
    provider = FakeProvider(sections=sections)
    script = ScriptWriter(provider, analysis, make_plan()).write()
    first_two = provider.section_requests[:2]
    assert first_two[0].feedback == []
    assert "no background process" in first_two[1].feedback[0]
    assert script.sections[0].beats[0].kind.value == "talk"  # repaired version used
    assert script.warnings == []


def test_unrepaired_problems_become_warnings(analysis):
    sections = make_sections()
    sections[1] = ScriptSectionDraft.model_validate({
        "beats": [{"kind": "stop_process", "narration": "Stop the server."}],
        "state_summary": "s",
    })
    script = ScriptWriter(FakeProvider(sections=sections), analysis, make_plan()).write()
    assert script.warnings and script.warnings[0].startswith("section 1:")


def test_resume_and_rewrite_from_section(analysis):
    first = FakeProvider()
    script = ScriptWriter(first, analysis, make_plan()).write()

    resumed = FakeProvider()
    partial = script.model_copy(deep=True)
    partial.sections = partial.sections[:2]
    ScriptWriter(resumed, analysis, make_plan()).write(existing=partial)
    assert [r.section_number for r in resumed.section_requests] == [3]

    rewrite = FakeProvider()
    ScriptWriter(rewrite, analysis, make_plan()).write(existing=script, from_section=2)
    assert [r.section_number for r in rewrite.section_requests] == [2, 3]


def test_resume_rejects_script_from_a_different_plan(analysis):
    script = ScriptWriter(FakeProvider(), analysis, make_plan()).write()
    changed = make_plan()
    changed.sections[0].title = "A different intro"
    with pytest.raises(ScriptError, match="--restart"):
        ScriptWriter(FakeProvider(), analysis, changed).write(existing=script)


def test_convergence_report(analysis):
    script = ScriptWriter(FakeProvider(), analysis, make_plan()).write()
    status = {item.path: item.status for item in script.convergence}
    assert status["app/main.py"] is ConvergenceStatus.DIFFERENT
    assert status["app/services/files.py"] is ConvergenceStatus.NOT_BUILT


def test_file_similarity_ignores_whitespace():
    assert file_similarity("a = 1\n\n\nb = 2\n", "a = 1   \nb = 2") == 1.0
    assert file_similarity("a = 1\n", "z = 9\n") == 0.0


# --- prompts ------------------------------------------------------------------------------------


def test_script_prompt_contains_state_references_and_feedback(analysis):
    provider = FakeProvider()
    ScriptWriter(provider, analysis, make_plan()).write()
    request = provider.section_requests[2].model_copy(update={"feedback": ["fix the highlight"]})
    prompt = prompts.build_script_section_prompt(request)
    assert "-> 3. Upload endpoint" in prompt
    assert "<<<FILE app/main.py>>>" in prompt
    assert "uv run fastapi dev app/main.py" in prompt  # running process
    assert "fix the highlight" in prompt
    assert "Aim for about 240 words" in prompt  # 120s * 2.5 w/s * 0.8


def test_plan_prompt_excludes_bookkeeping_fields(analysis):
    prompt = prompts.build_tutorial_plan_prompt(analysis, TutorialRequest(target_duration_minutes=25))
    assert "Target duration: about 1500 seconds" in prompt
    assert "potential_teaching_order" in prompt
    assert "scan_summary" not in prompt and "local_path" not in prompt


def test_project_documented_commands_are_allowed_in_its_script(settings):
    from tutorial_ai.models.script import ScriptSectionDraft
    from tutorial_ai.tutorial.script_writer import project_commands

    insights = make_insights(
        run_commands=[{"command": "yt-dlp --version && npm run dev", "description": "run"}],
        test_commands=[{"command": "ffmpeg -version", "description": "check ffmpeg"}],
    )
    source = resolve_source(str(EXAMPLE_REPO), settings.workspace_directory)
    analysis = RepositoryAnalyzer(settings, FakeProvider(insights)).analyze(source)
    assert project_commands(analysis) >= {"yt-dlp", "npm", "ffmpeg"}

    sections = make_sections()
    sections[1] = ScriptSectionDraft.model_validate({
        "beats": [{"kind": "run_command", "narration": "Let's check yt-dlp is installed.",
                   "command": "yt-dlp --version"}],
        "state_summary": "yt-dlp verified",
    })
    provider = FakeProvider(sections=sections)
    script = ScriptWriter(provider, analysis, make_plan()).write()
    assert script.warnings == []  # the command is not rejected for this project


def test_resume_drops_warnings_that_no_longer_apply(settings):
    """A command rejected by an older run is re-checked on resume, not carried forward."""
    insights = make_insights(run_commands=[{"command": "yt-dlp --version", "description": "check"}])
    source = resolve_source(str(EXAMPLE_REPO), settings.workspace_directory)
    analysis = RepositoryAnalyzer(settings, FakeProvider(insights)).analyze(source)

    script = ScriptWriter(FakeProvider(), analysis, make_plan()).write()
    script.warnings = ["section 1: beat 4 (run_command): command 'yt-dlp --version' is not allowed"]
    script.sections = script.sections[:1]

    resumed = ScriptWriter(FakeProvider(), analysis, make_plan()).write(existing=script)
    assert resumed.warnings == []


def test_rehearsal_feedback_reaches_the_ai_for_that_section(analysis):
    provider = FakeProvider()
    ScriptWriter(provider, analysis, make_plan(),
                 rehearsal_feedback={3: ["run_command s03_b02: uv run pytest: exit 2"]}).write()
    by_section = {r.section_number: r.feedback for r in provider.section_requests}
    assert by_section[1] == [] and by_section[2] == []
    assert by_section[3] and "uv run pytest: exit 2" in by_section[3][0]


def test_a_server_started_while_one_runs_gets_a_stop_beat_in_front():
    from tutorial_ai.models.script import BeatDraft, BeatKind, ScriptBeat
    from tutorial_ai.tutorial.script_writer import fix_kept_section, insert_server_stops
    from tutorial_ai.tutorial.workspace_state import WorkspaceState, check_beats

    serve = dict(kind="run_command", narration="Let's serve it.", command="python3 -m http.server 8000", background=True)
    beats = [
        BeatDraft.model_validate({"kind": "write_code", "narration": "Start.", "file": "index.html", "code_after": "<p>hi</p>\n"}),
        BeatDraft.model_validate(serve),
        BeatDraft.model_validate({"kind": "write_code", "narration": "More.", "file": "index.html", "code_after": "<p>hi!</p>\n"}),
        BeatDraft.model_validate({**serve, "narration": "Let's run the server again."}),
    ]
    state = WorkspaceState(allowed_commands=set(), package_dirs=set())
    assert any("still running" in p for p in check_beats(beats, state))

    fixed = insert_server_stops(beats, state)
    assert [b.kind for b in fixed] == [BeatKind.WRITE_CODE, BeatKind.RUN_COMMAND, BeatKind.WRITE_CODE,
                                       BeatKind.STOP_PROCESS, BeatKind.RUN_COMMAND]
    assert check_beats(fixed, state) == []

    kept = [ScriptBeat(id=f"s02_b{i:02d}", **b.model_dump()) for i, b in enumerate(beats, 1)]
    fixed_kept = fix_kept_section(kept, state)
    assert [b.id for b in fixed_kept] == ["s02_b01", "s02_b02", "s02_b03", "s02_b04_stop", "s02_b04"]
