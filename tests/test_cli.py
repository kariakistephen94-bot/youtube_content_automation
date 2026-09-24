import json
from pathlib import Path

import pytest
from conftest import EXAMPLE_REPO, FakeProvider, make_insights
from typer.testing import CliRunner

from tutorial_ai import cli, pipeline
from tutorial_ai.config import Settings

runner = CliRunner()


@pytest.fixture
def cli_settings(monkeypatch, tmp_path: Path) -> Settings:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    settings = Settings(
        _env_file=None,
        output_directory=tmp_path / "output",
        workspace_directory=tmp_path / "workspace",
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return settings


def test_dry_run_writes_context_without_ai(cli_settings: Settings):
    result = runner.invoke(cli.app, ["analyze", str(EXAMPLE_REPO), "--dry-run"])
    assert result.exit_code == 0, result.output
    project = cli_settings.output_directory / "fastapi-file-upload"
    assert (project / "repository_context.md").exists()
    assert (project / "logs" / "analyze.log").exists()
    assert not (project / "repository_analysis.json").exists()


def test_missing_api_key_fails_cleanly(cli_settings: Settings):
    result = runner.invoke(cli.app, ["analyze", str(EXAMPLE_REPO)])
    assert result.exit_code == 1


def test_analyze_writes_repository_analysis(cli_settings: Settings, monkeypatch):
    monkeypatch.setattr(pipeline, "create_ai_provider", lambda settings: FakeProvider(make_insights()))
    result = runner.invoke(cli.app, ["analyze", str(EXAMPLE_REPO), "--name", "Upload Demo"])
    assert result.exit_code == 0, result.output
    output = cli_settings.output_directory / "upload-demo" / "repository_analysis.json"
    data = json.loads(output.read_text())
    assert data["repository_name"] == "fastapi-file-upload"
    assert data["primary_language"] == "Python"


def test_bad_source_fails_cleanly(cli_settings: Settings):
    result = runner.invoke(cli.app, ["analyze", "https://gitlab.com/a/b"])
    assert result.exit_code == 1


def test_plan_script_scenes_pipeline(cli_settings: Settings, monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(pipeline, "create_ai_provider", lambda settings: provider)
    assert runner.invoke(cli.app, ["analyze", str(EXAMPLE_REPO), "--name", "demo"]).exit_code == 0

    result = runner.invoke(
        cli.app, ["plan", "demo", "--title", "Uploads!", "--audience", "beginner", "--duration", "6"]
    )
    assert result.exit_code == 0, result.output
    project = cli_settings.output_directory / "demo"
    assert json.loads((project / "tutorial_plan.json").read_text())["title"] == "Uploads!"

    result = runner.invoke(cli.app, ["script", "demo"])
    assert result.exit_code == 0, result.output
    script = json.loads((project / "script.json").read_text())
    scenes = json.loads((project / "scenes.json").read_text())
    assert len(script["sections"]) == 3
    assert [s["id"] for s in scenes["scenes"]] == ["scene_001", "scene_002", "scene_003"]

    # scenes can be rebuilt without the AI, e.g. with a different scene length
    result = runner.invoke(cli.app, ["scenes", "demo", "--max-scene-seconds", "20"])
    assert result.exit_code == 0, result.output
    assert len(json.loads((project / "scenes.json").read_text())["scenes"]) > 3

    # re-running script resumes: nothing left to write
    calls = len(provider.section_requests)
    assert runner.invoke(cli.app, ["script", "demo"]).exit_code == 0
    assert len(provider.section_requests) == calls


def test_commands_explain_missing_prerequisites(cli_settings: Settings, monkeypatch):
    monkeypatch.setattr(pipeline, "create_ai_provider", lambda settings: FakeProvider())
    result = runner.invoke(cli.app, ["plan", "nothing-here"])
    assert result.exit_code == 1
    runner.invoke(cli.app, ["analyze", str(EXAMPLE_REPO), "--name", "demo"])
    assert runner.invoke(cli.app, ["script", "demo"]).exit_code == 1  # no plan yet


def test_show_prints_readable_script(cli_settings: Settings, monkeypatch):
    monkeypatch.setattr(pipeline, "create_ai_provider", lambda settings: FakeProvider())
    runner.invoke(cli.app, ["analyze", str(EXAMPLE_REPO), "--name", "demo"])
    runner.invoke(cli.app, ["plan", "demo"])
    runner.invoke(cli.app, ["script", "demo"])
    result = runner.invoke(cli.app, ["show", "demo", "--section", "2"])
    assert result.exit_code == 0, result.output
    assert "WRITE_CODE" in result.output and "app/main.py" in result.output
    assert "Now let's create the app." in result.output
    assert "Section 3" not in result.output


def test_scenes_stay_in_step_when_script_stops_halfway(cli_settings: Settings, monkeypatch):
    from conftest import make_sections

    sections = make_sections()
    del sections[3]  # section 3 fails (KeyError inside the fake provider -> treated as a crash)
    provider = FakeProvider(sections=sections)
    monkeypatch.setattr(pipeline, "create_ai_provider", lambda settings: provider)
    runner.invoke(cli.app, ["analyze", str(EXAMPLE_REPO), "--name", "demo"])
    runner.invoke(cli.app, ["plan", "demo"])
    runner.invoke(cli.app, ["script", "demo"])
    project = cli_settings.output_directory / "demo"
    scenes = json.loads((project / "scenes.json").read_text())
    assert {s["section_number"] for s in scenes["scenes"]} == {1, 2}


def test_fix_from_rehearsal_rewrites_from_first_failing_section(cli_settings: Settings, monkeypatch, tmp_path):
    from datetime import UTC, datetime

    from tutorial_ai.tutorial.rehearsal import Check, RehearsalReport, SectionResult

    provider = FakeProvider()
    monkeypatch.setattr(pipeline, "create_ai_provider", lambda settings: provider)
    runner.invoke(cli.app, ["analyze", str(EXAMPLE_REPO), "--name", "demo"])
    runner.invoke(cli.app, ["plan", "demo"])
    runner.invoke(cli.app, ["script", "demo"])
    project = cli_settings.output_directory / "demo"
    report = RehearsalReport(ran_at=datetime.now(UTC), workspace="w", ok=False, sections=[
        SectionResult(section_number=2, title="First FastAPI app", ok=False,
                      checks=[Check(beat_id="s02_b05", kind="run_command", ok=False, detail="server did not start")])])
    (project / "rehearsal.json").write_text(report.model_dump_json())
    before = len(provider.section_requests)
    result = runner.invoke(cli.app, ["script", "demo", "--fix-from-rehearsal"])
    assert result.exit_code == 0, result.output
    rewritten = provider.section_requests[before:]
    assert [r.section_number for r in rewritten] == [2, 3]
    assert "server did not start" in rewritten[0].feedback[0]
