from pathlib import Path

from conftest import EXAMPLE_REPO, FakeProvider, make_insights, make_plan

from tutorial_ai.models.script import ScriptBeat, ScriptSection, TutorialScript
from tutorial_ai.repository.analyzer import RepositoryAnalyzer
from tutorial_ai.repository.clone import resolve_source
from tutorial_ai.tutorial.rehearsal import Rehearsal, RehearsalReport, syntax_check


def _analysis(settings):
    source = resolve_source(str(EXAMPLE_REPO), settings.workspace_directory)
    return RepositoryAnalyzer(settings, FakeProvider(make_insights(test_commands=[]))).analyze(source)


def _script(beats_by_section: dict[int, list[dict]]) -> TutorialScript:
    sections = []
    for number, beats in beats_by_section.items():
        sections.append(ScriptSection(
            section_number=number, title=f"S{number}", state_summary="s",
            beats=[ScriptBeat(id=f"s{number:02d}_b{i:02d}", **b) for i, b in enumerate(beats, 1)],
        ))
    return TutorialScript(title="T", sections=sections)


def test_syntax_check(tmp_path: Path):
    good = tmp_path / "a.py"; good.write_text("x = 1\n")
    bad = tmp_path / "b.py"; bad.write_text("def f(:\n")
    data = tmp_path / "c.json"; data.write_text("{not json")
    assert syntax_check(good) is None
    assert "line 1" in syntax_check(bad)
    assert syntax_check(data)
    other = tmp_path / "d.txt"; other.write_text("anything")
    assert syntax_check(other) is None


def test_rehearsal_runs_commands_and_reports_failures(settings, tmp_path):
    script = _script({
        1: [
            {"kind": "talk", "narration": "Hi."},
            {"kind": "write_code", "narration": "x", "file": "main.py", "code_after": 'print("hello")\n'},
            {"kind": "run_command", "narration": "x", "command": "python3 main.py", "expect_output": "hello"},
        ],
        2: [
            {"kind": "write_code", "narration": "x", "file": "broken.py", "code_after": "def f(:\n"},
            {"kind": "run_command", "narration": "x", "command": "python3 broken.py"},
            {"kind": "run_command", "narration": "x", "command": "rm -rf /"},
        ],
    })
    report = Rehearsal(_analysis(settings), tmp_path / "rehearsal").run(script)
    assert report.sections[0].ok
    assert not report.sections[1].ok and not report.ok
    kinds = [(c.kind, c.ok) for c in report.sections[1].checks]
    assert kinds == [("write_code", False), ("run_command", False), ("run_command", False)]
    assert "refused" in report.sections[1].checks[2].detail
    assert (tmp_path / "rehearsal" / "main.py").exists()
    assert RehearsalReport.model_validate_json(report.model_dump_json()).failures


def test_background_server_is_started_probed_and_stopped(settings, tmp_path):
    server = (
        "import http.server, sys\n"
        "print('Serving now', flush=True)\n"
        "http.server.HTTPServer(('127.0.0.1', 8765 + 300), http.server.SimpleHTTPRequestHandler).serve_forever()\n"
    )
    script = _script({
        1: [
            {"kind": "write_code", "narration": "x", "file": "serve.py", "code_after": server},
            {"kind": "run_command", "narration": "x", "command": "python3 serve.py", "background": True,
             "expect_output": "Serving now"},
            {"kind": "open_browser", "narration": "x", "url": "http://127.0.0.1:9065/"},
            {"kind": "write_code", "narration": "x", "file": "note.txt", "code_after": "edit while running\n"},
        ],
        2: [{"kind": "stop_process", "narration": "Stop it."}],
    })
    rehearsal = Rehearsal(_analysis(settings), tmp_path / "rehearsal")
    report = rehearsal.run(script)
    assert report.ok, [c.detail for s in report.sections for c in s.checks if not c.ok]
    kinds = [c.kind for c in report.sections[0].checks]
    assert kinds == ["write_code", "run_command", "open_browser", "write_code", "server_reload"]
    assert [c.kind for c in report.sections[1].checks] == ["stop_process"]
    assert rehearsal._processes == []  # nothing left running


def test_port_conflict_is_reported_before_starting_a_server(settings, tmp_path):
    import socket

    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    script = _script({1: [
        {"kind": "run_command", "narration": "x", "command": "python3 -c 'import time; time.sleep(30)'",
         "background": True},
        {"kind": "open_browser", "narration": "x", "url": f"http://127.0.0.1:{port}/docs"},
    ]})
    try:
        report = Rehearsal(_analysis(settings), tmp_path / "rehearsal").run(script)
    finally:
        blocker.close()
    check = report.sections[0].checks[0]
    assert not check.ok and f"port {port} is already in use" in check.detail


def test_only_selected_sections_are_checked(settings, tmp_path):
    script = _script({
        1: [{"kind": "write_code", "narration": "x", "file": "a.py", "code_after": "a = 1\n"}],
        2: [{"kind": "run_command", "narration": "x", "command": "python3 a.py"}],
    })
    report = Rehearsal(_analysis(settings), tmp_path / "rehearsal").run(script, sections=[2])
    assert [s.section_number for s in report.sections] == [2]
    assert report.ok  # section 1's file was still written so section 2 works


def test_tutorial_commands_do_not_inherit_our_virtualenv(monkeypatch):
    from tutorial_ai.tutorial.rehearsal import clean_env

    monkeypatch.setenv("VIRTUAL_ENV", "/repo/.venv")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/repo/.venv")
    monkeypatch.setenv("PATH", "/repo/.venv/bin:/usr/bin")
    env = clean_env()
    assert "VIRTUAL_ENV" not in env and "UV_PROJECT_ENVIRONMENT" not in env
    assert env["PATH"] == "/usr/bin"


def test_missing_expected_text_is_a_note_not_a_failure(settings, tmp_path):
    script = _script({1: [{"kind": "run_command", "narration": "x", "command": "echo installed",
                           "expect_output": "Added"}]})
    report = Rehearsal(_analysis(settings), tmp_path / "rehearsal").run(script)
    check = report.sections[0].checks[0]
    assert check.ok and "expected text" in check.detail


def test_stale_parent_project_is_wiped_before_rehearsing(settings, tmp_path):
    area = tmp_path / "rehearsal"
    (area / "old").mkdir(parents=True)
    (area / "pyproject.toml").write_text('[project]\nname = "stale"\n')
    script = _script({1: [{"kind": "write_code", "narration": "x", "file": "a.py", "code_after": "a = 1\n"}]})
    Rehearsal(_analysis(settings), area / "repo").run(script)
    assert not (area / "pyproject.toml").exists() and not (area / "old").exists()
    assert (area / "repo" / "a.py").exists()


def test_feedback_by_section_skips_environment_problems():
    from datetime import UTC, datetime

    from tutorial_ai.tutorial.rehearsal import Check, RehearsalReport, SectionResult

    report = RehearsalReport(ran_at=datetime.now(UTC), workspace="w", ok=False, sections=[
        SectionResult(section_number=2, title="a", ok=False, checks=[
            Check(beat_id="s02_b07", kind="run_command", ok=False, detail="uv run x: port 8000 is already in use by another program"),
        ]),
        SectionResult(section_number=7, title="b", ok=False, checks=[
            Check(beat_id="s07_b05", kind="run_command", ok=False, detail="uv run pytest: exit 2\nModuleNotFoundError: No module named 'app'"),
        ]),
    ], final_tests=[Check(beat_id="final_tests", kind="final_tests", ok=False, detail="uv run pytest: exit 2")])
    feedback = report.feedback_by_section()
    assert list(feedback) == [7] and len(feedback[7]) == 2  # port problem dropped; final tests -> last section
    assert "No module named 'app'" in feedback[7][0]


def test_failure_detail_keeps_the_error_lines_not_just_the_tail():
    from tutorial_ai.tutorial.rehearsal import summarize_output

    output = "collecting ...\nImportError while importing test module\nE   ModuleNotFoundError: No module named 'app'\n" + "warning line\n" * 200
    summary = summarize_output(output, tail=100)
    assert "ModuleNotFoundError: No module named 'app'" in summary
    assert summary.endswith("warning line\n".rstrip("\n")) or "warning line" in summary


def test_background_server_keeps_answering_after_lots_of_log_output(settings, tmp_path):
    """A chatty server (more output than a pipe buffer holds) must not hang."""
    server = (
        "import http.server, sys\n"
        "print('Serving now', flush=True)\n"
        "for i in range(3000): print('log line ' * 10, i, flush=True)\n"  # ~300 KB, more than the pipe
        "http.server.HTTPServer(('127.0.0.1', 9066), http.server.SimpleHTTPRequestHandler).serve_forever()\n"
    )
    script = _script({1: [
        {"kind": "write_code", "narration": "x", "file": "serve.py", "code_after": server},
        {"kind": "run_command", "narration": "x", "command": "python3 serve.py", "background": True,
         "expect_output": "Serving now"},
        {"kind": "talk", "narration": "Wait a moment."},
        {"kind": "open_browser", "narration": "x", "url": "http://127.0.0.1:9066/"},
    ]})
    report = Rehearsal(_analysis(settings), tmp_path / "rehearsal").run(script)
    assert report.ok, [c.detail[:200] for s in report.sections for c in s.checks if not c.ok]


_DEMO_API = '''
import json, http.server
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body):
        data = json.dumps(body).encode(); self.send_response(code)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.path == "/openapi.json":
            self._send(200, {"paths": {"/upload": {"post": {}}, "/": {"get": {}}}})
        elif self.path == "/docs":
            self._send(200, {"docs": True})
        else:
            self._send(200, {"status": "ok"})
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path != "/upload":
            self._send(404, {"detail": "Not Found"}); return
        if b"filename=" not in body:
            self._send(422, {"detail": "file field required"}); return
        self._send(200, {"received": body.count(b"Hello")})
print("ready", flush=True)
http.server.HTTPServer(("127.0.0.1", PORT), H).serve_forever()
'''


def test_try_endpoint_sends_the_demo_request_and_reports_wrong_paths(settings, tmp_path):
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    docs = f"http://127.0.0.1:{port}/docs"
    upload = {"name": "file", "value": "@hello.txt"}
    sample = {"name": "hello.txt", "content": "Hello Hello\n"}
    script = _script({
        1: [
            {"kind": "write_code", "narration": "x", "file": "api.py", "code_after": _DEMO_API.replace("PORT", str(port))},
            {"kind": "run_command", "narration": "x", "command": "python3 api.py", "background": True, "expect_output": "ready"},
            {"kind": "open_browser", "narration": "x", "url": docs},
            {"kind": "try_endpoint", "narration": "x", "url": docs, "method": "post", "path": "/upload",
             "params": [upload], "sample_files": [sample]},
            {"kind": "try_endpoint", "narration": "x", "url": docs, "method": "post", "path": "/uploads",
             "params": [upload], "sample_files": [sample]},
            {"kind": "try_endpoint", "narration": "x", "url": docs, "method": "post", "path": "/upload",
             "params": [{"name": "note", "value": "no file"}]},
            {"kind": "try_endpoint", "narration": "x", "url": docs, "method": "get", "path": "/"},
        ],
        2: [{"kind": "stop_process", "narration": "Stop it."}],
    })
    report = Rehearsal(_analysis(settings), tmp_path / "rehearsal").run(script)
    checks = {c.beat_id: c for c in report.sections[0].checks if c.kind == "try_endpoint"}
    assert checks["s01_b04"].ok and "HTTP 200" in checks["s01_b04"].detail and '"received": 2' in checks["s01_b04"].detail
    assert not checks["s01_b05"].ok and "no such endpoint" in checks["s01_b05"].detail and "POST /upload" in checks["s01_b05"].detail
    assert not checks["s01_b06"].ok and "HTTP 422" in checks["s01_b06"].detail and "field names" in checks["s01_b06"].detail
    assert checks["s01_b07"].ok and "GET / -> HTTP 200" in checks["s01_b07"].detail
    assert not report.ok and len(report.feedback_by_section()[1]) == 2  # the two bad requests, fed back to the AI


def test_multipart_encoding_carries_fields_and_files():
    from tutorial_ai.tutorial.rehearsal import encode_multipart

    body, content_type = encode_multipart({"note": "hi"}, {"file": ("hello.txt", b"Hello")})
    boundary = content_type.split("boundary=")[1].encode()
    assert content_type.startswith("multipart/form-data; boundary=")
    assert body.count(b"--" + boundary) == 3 and body.endswith(b"--" + boundary + b"--\r\n")
    assert b'name="note"\r\n\r\nhi\r\n' in body
    assert b'name="file"; filename="hello.txt"\r\nContent-Type: text/plain\r\n\r\nHello\r\n' in body


def test_openapi_check_waits_for_a_reloading_server(monkeypatch):
    """Right after a save the dev server still serves the old app; the route appears a moment later."""
    import io
    import json as _json

    from tutorial_ai.tutorial import rehearsal as rh

    specs = iter([{"paths": {"/": {"get": {}}}}] * 2 + [{"paths": {"/": {"get": {}}, "/upload": {"post": {}}}}])

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(rh.urllib.request, "urlopen", lambda url, timeout: Response(_json.dumps(next(specs)).encode()))
    monkeypatch.setattr(rh.time, "sleep", lambda s: None)
    assert rh._check_openapi("http://127.0.0.1:8000", "post", "/upload", grace=5) is None

    specs = iter([{"paths": {"/": {"get": {}}}}] * 1000)
    clock = iter(range(0, 10000))
    monkeypatch.setattr(rh.time, "monotonic", lambda: next(clock))
    problem = rh._check_openapi("http://127.0.0.1:8000", "post", "/upload", grace=5)
    assert problem and "GET /" in problem


def test_rejected_sample_file_gets_an_actionable_hint():
    from datetime import UTC, datetime

    from tutorial_ai.tutorial.rehearsal import Check, RehearsalReport, SectionResult

    report = RehearsalReport(ran_at=datetime.now(UTC), workspace="w", ok=False, sections=[
        SectionResult(section_number=6, title="t", ok=False, checks=[
            Check(beat_id="s06_b07", kind="try_endpoint", ok=False,
                  detail='POST /uploads -> HTTP 400: {"detail":"Unsupported file type"}'),
        ]),
    ])
    feedback = report.feedback_by_section()[6][0]
    assert "content type comes from its file NAME" in feedback and "%PDF-1.4" in feedback
