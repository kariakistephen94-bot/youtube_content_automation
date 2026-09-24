import time
from datetime import UTC, datetime

import pytest
from conftest import EXAMPLE_REPO, FakeProvider
from fastapi.testclient import TestClient

from tutorial_ai import github, pipeline
from tutorial_ai.api.app import create_app
from tutorial_ai.config import Settings

H = {"X-Tutorial-AI": "1"}


@pytest.fixture
def api(tmp_path, monkeypatch, isolated_config_dir):
    monkeypatch.setattr(pipeline, "create_ai_provider", lambda settings: FakeProvider())
    settings = Settings(
        _env_file=None, output_directory=tmp_path / "output",
        workspace_directory=tmp_path / "workspace", config_directory=isolated_config_dir,
        github_client_id="Iv1.test", openai_api_key="sk-test-1234567890abcdefghij",
    )
    app = create_app(settings)
    yield TestClient(app)
    app.state.jobs.close()


def _wait(api, job_id: str) -> dict:
    for _ in range(200):
        job = api.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_state_changing_requests_need_the_csrf_header(api):
    assert api.post("/api/jobs/analyze", json={"source": "x"}).status_code == 403
    assert api.post("/api/github/disconnect").status_code == 403
    assert api.get("/api/health").status_code == 200


def test_full_pipeline_through_jobs(api):
    job = api.post("/api/jobs/analyze", json={"source": str(EXAMPLE_REPO), "name": "demo"}, headers=H).json()
    done = _wait(api, job["id"])
    assert done["status"] == "succeeded", done
    assert any("Analysing" in line for line in done["logs"])  # pipeline logs are captured

    done = _wait(api, api.post("/api/jobs/plan", json={"project": "demo", "title": "T"}, headers=H).json()["id"])
    assert done["status"] == "succeeded", done
    done = _wait(api, api.post("/api/jobs/script", json={"project": "demo"}, headers=H).json()["id"])
    assert done["status"] == "succeeded", done

    projects = api.get("/api/projects").json()
    assert projects[0]["name"] == "demo" and projects[0]["script_sections"] == 3
    detail = api.get("/api/projects/demo").json()
    assert detail["plan"]["title"] == "T"
    assert len(detail["script"]["sections"]) == 3
    assert detail["typed_code"]["s02_b02"].startswith("from fastapi import FastAPI")


def test_failures_are_reported_not_raised(api):
    job = api.post("/api/jobs/plan", json={"project": "missing"}, headers=H).json()
    done = _wait(api, job["id"])
    assert done["status"] == "failed"
    assert "repository_analysis.json" in done["error"]


def test_one_job_at_a_time(api):
    import threading

    release = threading.Event()
    app_jobs = api.app.state.jobs
    app_jobs.submit("slow", lambda step: release.wait(5) and None)
    response = api.post("/api/jobs/analyze", json={"source": str(EXAMPLE_REPO)}, headers=H)
    assert response.status_code == 409
    release.set()


def test_invalid_project_names_are_rejected(api):
    assert api.get("/api/projects/..%2F..%2Fetc").status_code in (400, 404)
    assert api.post("/api/jobs/plan", json={"project": "../x"}, headers=H).status_code == 400


def test_github_device_flow(api, monkeypatch, isolated_config_dir):
    monkeypatch.setattr(github, "start_device_flow", lambda client_id: github.DeviceCode(
        device_code="dc-secret", user_code="ABCD-1234",
        verification_uri="https://github.com/login/device", expires_in=900, interval=5))
    results = iter([
        github.PollResult(status="pending"),
        github.PollResult(status="authorized", token=github.GitHubToken(
            access_token="gho_secret", created_at=datetime.now(UTC))),
    ])
    monkeypatch.setattr(github, "poll_device_flow", lambda client_id, device_code: next(results))
    monkeypatch.setattr(github.GitHubClient, "get_user",
                        lambda self: github.GitHubUser(login="me", html_url="https://github.com/me"))

    start = api.post("/api/github/device/start", headers=H).json()
    assert start == {"user_code": "ABCD-1234", "verification_uri": "https://github.com/login/device",
                     "expires_in": 900, "interval": 5}  # device_code never leaves the server
    assert api.post("/api/github/device/poll", json={"user_code": "ABCD-1234"}, headers=H).json()["status"] == "pending"
    done = api.post("/api/github/device/poll", json={"user_code": "ABCD-1234"}, headers=H).json()
    assert done["status"] == "authorized" and done["user"]["login"] == "me"

    status = api.get("/api/github/status").json()
    assert status["connected"] and status["user"]["login"] == "me"
    assert "gho_secret" not in str(status)
    assert github.load_token(isolated_config_dir).login == "me"

    api.post("/api/github/disconnect", headers=H)
    assert api.get("/api/github/status").json()["connected"] is False


def test_github_not_configured(api, isolated_config_dir, tmp_path):
    app = create_app(Settings(_env_file=None, config_directory=isolated_config_dir,
                              output_directory=tmp_path / "o"))
    client = TestClient(app)
    assert client.get("/api/github/status").json()["configured"] is False
    response = client.post("/api/github/device/start", headers=H)
    assert response.status_code == 400 and "GITHUB_CLIENT_ID" in response.json()["detail"]
    app.state.jobs.close()


def test_model_override_is_used_for_the_job(api, monkeypatch):
    from tutorial_ai.api import app as app_module

    seen: list[str] = []

    def fake_create(settings):
        from tutorial_ai.config import ai_model
        seen.append(ai_model(settings))
        return FakeProvider()

    monkeypatch.setattr(app_module, "create_ai_provider", fake_create)
    job = api.post(
        "/api/jobs/analyze",
        json={"source": str(EXAMPLE_REPO), "name": "demo", "model": "gpt-4.1"},
        headers=H,
    ).json()
    assert _wait(api, job["id"])["status"] == "succeeded"
    assert seen == ["gpt-4.1"]


def test_narrate_job_and_audio_serving(api, monkeypatch):
    from tutorial_ai.providers import tts as tts_module
    from test_narration import FakeTTS

    monkeypatch.setattr(tts_module, "create_tts_provider", lambda settings, provider=None, voice=None: FakeTTS())
    for kind, body in [("analyze", {"source": str(EXAMPLE_REPO), "name": "demo"}),
                       ("plan", {"project": "demo"}), ("script", {"project": "demo"})]:
        assert _wait(api, api.post(f"/api/jobs/{kind}", json=body, headers=H).json()["id"])["status"] == "succeeded"
    done = _wait(api, api.post("/api/jobs/narrate", json={"project": "demo"}, headers=H).json()["id"])
    assert done["status"] == "succeeded", done
    detail = api.get("/api/projects/demo").json()
    entry = detail["narration"]["entries"][0]
    assert entry["duration_seconds"] > 0
    audio = api.get(f"/api/projects/demo/files/{entry['audio_path']}")
    assert audio.status_code == 200 and audio.content[:4] == b"RIFF"
    assert api.get("/api/projects/demo/files/../../etc/passwd").status_code in (403, 404)
    assert api.get("/api/projects/demo/files/repository_analysis.py").status_code in (403, 404)


def test_job_progress_is_derived_from_log_lines():
    from datetime import UTC, datetime

    from tutorial_ai.api.jobs import Job

    job = Job(id="j", kind="script", created_at=datetime.now(UTC))
    job.note("Writing section 3/7: Upload endpoint")
    assert job.progress == pytest.approx(2 / 7) and job.progress_label.startswith("Writing section 3/7")
    job.note("[12/30] s02_b03_n1: 4.1s")
    assert job.progress == pytest.approx(12 / 30)
    job.note("no numbers here")
    assert job.progress == pytest.approx(12 / 30)
