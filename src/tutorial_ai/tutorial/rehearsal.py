"""Rehearsal: run the script for real, off camera, and report what breaks.

Every section is replayed in a throwaway workspace: files are written and syntax-checked, commands
are run (allowlisted only), background servers are started and their URLs checked, and the
project's own test commands run at the end. This catches broken commands, import errors and
crashed servers before a single frame is recorded.
"""

import ast
import json
import os
import re
import shutil
import signal
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from tutorial_ai.models.repository import RepositoryAnalysis
from tutorial_ai.models.script import BeatKind, ScriptBeat, TutorialScript
from tutorial_ai.security.command_validator import validate_command
from tutorial_ai.tutorial.script_writer import project_commands
from tutorial_ai.utils.logging import get_logger

log = get_logger("tutorial.rehearsal")

COMMAND_TIMEOUT = 180
SERVER_READY_TIMEOUT = 40
URL_TIMEOUT = 10


class Check(BaseModel):
    beat_id: str
    kind: str
    ok: bool
    detail: str = ""
    seconds: float = 0.0


class SectionResult(BaseModel):
    section_number: int
    title: str
    ok: bool
    checks: list[Check] = Field(default_factory=list)
    seconds: float = 0.0


class RehearsalReport(BaseModel):
    ran_at: datetime
    workspace: str
    ok: bool
    sections: list[SectionResult] = Field(default_factory=list)
    final_tests: list[Check] = Field(default_factory=list)

    def feedback_by_section(self) -> dict[int, list[str]]:
        """Failing checks as plain-language feedback, keyed by section (final tests -> last section)."""
        feedback: dict[int, list[str]] = {}
        last = max((s.section_number for s in self.sections), default=0)
        for number, check in self.failures:
            target = number or last
            if "already in use by another program" in check.detail:
                continue  # an environment problem, not the script's fault
            text = f"{check.kind} {check.beat_id}: {check.detail[:1200]}"
            if check.kind == "try_endpoint" and re.search(r"HTTP (400|415|422)", check.detail) and re.search(
                r"unsupported|file type|content.type|not allowed|invalid", check.detail, re.IGNORECASE
            ):
                text += (" HINT: the app rejected the demo request's sample file. A sample file's content "
                         "type comes from its file NAME, so pick a name/type the app's validation accepts "
                         "(read the validation code in the workspace), e.g. `sample.pdf` with content "
                         "starting `%PDF-1.4` or `photo.png`, and update the narration to match. To show the "
                         "rejection on purpose instead, do that in a separate try_endpoint and say so.")
            if "ModuleNotFoundError" in check.detail or "No module named" in check.detail:
                text += (" HINT: the tutorial project's packages are not importable from the tests. Add "
                         "`__init__.py` files to the package folders and to `tests/` (e.g. with a "
                         "run_command `touch app/__init__.py tests/__init__.py`) exactly as the reference "
                         "repository has them, before running pytest.")
            feedback.setdefault(target, []).append(text)
        return feedback

    @property
    def failing_sections(self) -> list[int]:
        return sorted(self.feedback_by_section())

    @property
    def failures(self) -> list[tuple[int, Check]]:
        return [(s.section_number, c) for s in self.sections for c in s.checks if not c.ok] + [
            (0, c) for c in self.final_tests if not c.ok
        ]


class Rehearsal:
    def __init__(self, analysis: RepositoryAnalysis, workspace: Path,
                 on_progress: Callable[[str], None] | None = None) -> None:
        self._allowed = project_commands(analysis)
        self._analysis = analysis
        self.workspace = workspace
        self._progress = on_progress or (lambda message: None)
        self._processes: list[tuple[str, subprocess.Popen]] = []
        self._readers: dict[int, "_OutputReader"] = {}
        self._known_urls: list[str] = []
        self._script_sections: list = []

    def run(self, script: TutorialScript, sections: list[int] | None = None) -> RehearsalReport:
        # Wipe the whole rehearsal area (parent), never just the repo folder: a stale project in
        # the parent would make uv treat the tutorial as a nested workspace member.
        area = self.workspace.parent if self.workspace.parent.name == "rehearsal" else self.workspace
        if area.exists():
            shutil.rmtree(area)
        self.workspace.mkdir(parents=True)
        report = RehearsalReport(ran_at=datetime.now(UTC), workspace=str(self.workspace), ok=True)
        self._script_sections = script.sections
        try:
            for section in script.sections:
                if sections and section.section_number not in sections:
                    for beat in section.beats:  # keep the workspace consistent without checking
                        self._apply_silently(beat)
                    continue
                self._progress(f"Section {section.section_number}: {section.title}")
                started = time.monotonic()
                result = SectionResult(section_number=section.section_number, title=section.title, ok=True)
                for beat in section.beats:
                    check = self._run_beat(beat)
                    if check is None:
                        continue
                    result.checks.append(check)
                    status = "ok" if check.ok else "FAILED"
                    self._progress(f"  {check.beat_id} {check.kind}: {status} {check.detail[:120]}")
                    if not check.ok:
                        result.ok = False
                # a running dev server reloads on file changes: make sure it still answers
                if any(b.kind is BeatKind.WRITE_CODE for b in section.beats):
                    for url in self._server_urls():
                        check = Check(beat_id=f"s{section.section_number:02d}", kind="server_reload",
                                      ok=True, detail=url)
                        error = _probe_url(url)
                        if error:
                            check.ok, check.detail = False, f"{url}: {error}{self._server_log_excerpt()}"
                            result.ok = False
                            self._progress(f"  server check after edits: FAILED {error}")
                        result.checks.append(check)
                result.seconds = round(time.monotonic() - started, 1)
                report.sections.append(result)
                report.ok = report.ok and result.ok
            report.final_tests = self._run_final_tests()
            report.ok = report.ok and all(c.ok for c in report.final_tests)
        finally:
            self._stop_all()
        log.info("Rehearsal %s: %d failing checks", "passed" if report.ok else "FAILED",
                 len(report.failures))
        return report

    # --- beats --------------------------------------------------------------------------------

    def _run_beat(self, beat: ScriptBeat) -> Check | None:
        started = time.monotonic()
        check: Check | None
        match beat.kind:
            case BeatKind.WRITE_CODE:
                path = self.workspace / beat.file
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(beat.code_after or "", encoding="utf-8")
                error = syntax_check(path)
                check = Check(beat_id=beat.id, kind="write_code", ok=error is None,
                              detail=f"{beat.file}: {error}" if error else beat.file)
            case BeatKind.DELETE_FILE:
                (self.workspace / beat.file).unlink(missing_ok=True)
                check = Check(beat_id=beat.id, kind="delete_file", ok=True, detail=beat.file)
            case BeatKind.RUN_COMMAND:
                check = self._run_command(beat)
            case BeatKind.STOP_PROCESS:
                stopped = self._stop_last()
                self._known_urls.clear()
                check = Check(beat_id=beat.id, kind="stop_process", ok=True, detail=stopped or "nothing running")
            case BeatKind.OPEN_BROWSER:
                if beat.url and beat.url not in self._known_urls:
                    self._known_urls.append(beat.url)
                error = _probe_url(beat.url or "")
                if error:
                    error += self._server_log_excerpt()
                check = Check(beat_id=beat.id, kind="open_browser", ok=error is None,
                              detail=f"{beat.url}: {error}" if error else beat.url or "")
            case BeatKind.TRY_ENDPOINT:
                # The same request the browser will make on camera, sent directly to the server.
                outcome, error = try_request(beat)
                if error:
                    error += self._server_log_excerpt()
                check = Check(beat_id=beat.id, kind="try_endpoint", ok=error is None,
                              detail=error or outcome)
            case _:
                return None
        check.seconds = round(time.monotonic() - started, 1)
        return check

    def _apply_silently(self, beat: ScriptBeat) -> None:
        if beat.kind is BeatKind.WRITE_CODE and beat.file:
            path = self.workspace / beat.file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(beat.code_after or "", encoding="utf-8")
        elif beat.kind is BeatKind.DELETE_FILE and beat.file:
            (self.workspace / beat.file).unlink(missing_ok=True)
        elif beat.kind is BeatKind.RUN_COMMAND:
            self._run_command(beat)
        elif beat.kind is BeatKind.STOP_PROCESS:
            self._stop_last()

    def _run_command(self, beat: ScriptBeat) -> Check:
        command = beat.command or ""
        verdict = validate_command(command, self._allowed)
        if not verdict.allowed:
            return Check(beat_id=beat.id, kind="run_command", ok=False,
                         detail=f"{command!r} refused: {verdict.reason}")
        env = clean_env()
        if beat.background:
            port = self._expected_port(beat)
            if port and port_in_use(port):
                if self._processes:  # our own earlier server still holds the port: a script bug
                    running = self._processes[-1][0]
                    return Check(beat_id=beat.id, kind="run_command", ok=False,
                                 detail=f"{command}: port {port} is still held by the tutorial's own "
                                        f"earlier server `{running}`. Add a stop_process beat before "
                                        f"starting another server.")
                return Check(beat_id=beat.id, kind="run_command", ok=False,
                             detail=f"{command}: port {port} is already in use by another program on "
                                    f"this Mac. Stop it (another dev server?) and rehearse again.")
            process = subprocess.Popen(
                command, shell=True, cwd=self.workspace, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, start_new_session=True,
            )
            reader = _OutputReader(process)  # drains the pipe for the process's whole life
            self._processes.append((command, process))
            self._readers[process.pid] = reader
            ready, output = _wait_for_server(process, reader, beat.expect_output, SERVER_READY_TIMEOUT)
            if ready:
                return Check(beat_id=beat.id, kind="run_command", ok=True, detail=f"{command} (running)")
            return Check(beat_id=beat.id, kind="run_command", ok=False,
                         detail=f"{command}: server did not start.\n{summarize_output(output)}")
        try:
            completed = subprocess.run(
                command, shell=True, cwd=self.workspace, env=env, capture_output=True, text=True,
                timeout=COMMAND_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return Check(beat_id=beat.id, kind="run_command", ok=False,
                         detail=f"{command}: still running after {COMMAND_TIMEOUT}s")
        output = (completed.stdout + completed.stderr).strip()
        ok = completed.returncode == 0
        detail = command if ok else f"{command}: exit {completed.returncode}\n{summarize_output(output)}"
        if ok and beat.expect_output and beat.expect_output not in output:
            detail += f" (note: expected text {beat.expect_output!r} not in the output)"
        return Check(beat_id=beat.id, kind="run_command", ok=ok, detail=detail)

    def _run_final_tests(self) -> list[Check]:
        has_tests = any(p.name.startswith("test") for p in self.workspace.rglob("*.py")) or (
            self.workspace / "tests").exists()
        if not has_tests:
            return []
        checks = []
        for spec in self._analysis.test_commands:
            fake = ScriptBeat(id="final_tests", kind=BeatKind.RUN_COMMAND, narration="", command=spec.command)
            self._progress(f"Final tests: {spec.command}")
            check = self._run_command(fake)
            check.kind = "final_tests"
            checks.append(check)
        return checks

    def _server_log_excerpt(self) -> str:
        """The running server's recent output - usually the traceback behind a dead URL."""
        if not self._processes:
            return ""
        command, process = self._processes[-1]
        reader = self._readers.get(process.pid)
        if reader is None:
            return ""
        state = "running" if process.poll() is None else f"exited with code {process.returncode}"
        return f"\nServer `{command}` ({state}) recently printed:\n{summarize_output(reader.text(), tail=1500)}"

    def _expected_port(self, beat: ScriptBeat) -> int | None:
        """The port the next browser URL in the script points at (what this server should own)."""
        seen = False
        for section in self._script_sections:
            for other in section.beats:
                if other.id == beat.id:
                    seen = True
                elif seen and other.kind in (BeatKind.OPEN_BROWSER, BeatKind.TRY_ENDPOINT) and other.url:
                    return url_port(other.url)
        return None

    # --- processes ----------------------------------------------------------------------------

    def _server_urls(self) -> list[str]:
        return list(self._known_urls) if self._processes else []

    def _stop_last(self) -> str | None:
        if not self._processes:
            return None
        command, process = self._processes.pop()
        _terminate(process)
        return command

    def _stop_all(self) -> None:
        while self._processes:
            self._stop_last()


def clean_env() -> dict[str, str]:
    """Environment for tutorial commands: no trace of tutorial-ai's own uv/virtualenv."""
    env = {k: v for k, v in os.environ.items()
           if k not in {"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH"} and not k.startswith("UV_")}
    venv_bin = os.environ.get("VIRTUAL_ENV", "") + "/bin"
    if venv_bin != "/bin":
        env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if p != venv_bin)
    env.update({"PYTHONUNBUFFERED": "1", "CI": "1", "NO_COLOR": "1"})
    return env


_ERROR_LINE = re.compile(r"Error|error:|Traceback|FAILED|failed|Exception|exit code|not found", re.IGNORECASE)


def summarize_output(output: str, tail: int = 600, max_error_lines: int = 15) -> str:
    """The lines that explain a failure (errors/tracebacks) plus the end of the output."""
    lines = output.splitlines()
    errors = [line for line in lines if _ERROR_LINE.search(line)][:max_error_lines]
    ending = output[-tail:]
    parts = []
    if errors:
        parts.append("Key lines:\n" + "\n".join(errors))
    parts.append("Output ends with:\n" + ending)
    return "\n".join(parts)


def url_port(url: str) -> int | None:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.port:
        return parsed.port
    return {"http": 80, "https": 443}.get(parsed.scheme)


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def syntax_check(path: Path) -> str | None:
    """Cheap per-file check: Python and JSON are parsed, JavaScript goes through `node --check`."""
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            ast.parse(text, filename=str(path))
        elif path.suffix == ".json":
            json.loads(text)
        elif path.suffix in {".js", ".mjs", ".cjs"} and shutil.which("node"):
            result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "syntax error"
    except SyntaxError as exc:
        return f"line {exc.lineno}: {exc.msg}"
    except (ValueError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    return None


def _probe_url(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=URL_TIMEOUT) as response:
            if response.status >= 500:
                return f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        if exc.code >= 500:
            return f"HTTP {exc.code}"
    except Exception as exc:  # connection refused, timeout, ...
        return f"{type(exc).__name__}: {exc}"
    return None


# --- try_endpoint: the demo request, sent for real ------------------------------------------------


def try_request(beat: ScriptBeat) -> tuple[str, str | None]:
    """Send the request a try_endpoint beat describes (as Swagger UI would) and return
    (outcome, error). The endpoint is first looked up in the app's OpenAPI schema, so a wrong path
    or method is reported precisely, with the endpoints that do exist."""
    from urllib.parse import urlencode, urlsplit, urlunsplit

    method = (beat.method or "get").upper()
    path = beat.path or "/"
    parts = urlsplit(beat.url or "")
    base = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    label = f"{method} {path}"

    spec_error = _check_openapi(base, method, path)
    if spec_error:
        return label, f"{label}: {spec_error}"

    fields = beat.param_values
    samples = beat.sample_file_contents
    files = {name: value[1:] for name, value in fields.items() if value.startswith("@")}
    plain = {name: value for name, value in fields.items() if not value.startswith("@")}
    for name, file_name in files.items():
        if file_name not in samples:
            return label, f"{label}: params {name!r} uses sample file {file_name!r} which is not in sample_files"

    headers: dict[str, str] = {}
    data: bytes | None = None
    query = ""
    if beat.body is not None:
        data = beat.body.encode("utf-8")
        headers["Content-Type"] = "application/json"
        query = urlencode(plain)
    elif files:
        data, headers["Content-Type"] = encode_multipart(
            plain, {name: (file_name, samples[file_name].encode("utf-8")) for name, file_name in files.items()}
        )
    elif method in ("GET", "DELETE", "HEAD"):
        query = urlencode(plain)
    elif plain:
        data = urlencode(plain).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    url = base + path + (f"?{query}" if query else "")
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=URL_TIMEOUT) as response:
            status, body = response.status, response.read(2000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read(2000).decode("utf-8", "replace")
    except Exception as exc:
        return label, f"{label}: {type(exc).__name__}: {exc}"
    summary = " ".join(body.split())[:300]
    if status >= 400:
        hint = " (the request the script describes is not what the endpoint accepts: check the field names and values)" if status == 422 else ""
        return label, f"{label} -> HTTP {status}{hint}: {summary}"
    return f"{label} -> HTTP {status} {summary}", None


RELOAD_GRACE_SECONDS = 10.0  # a dev server needs a moment to reload after a file is saved


def _check_openapi(base: str, method: str, path: str, grace: float = RELOAD_GRACE_SECONDS) -> str | None:
    """None if the app's /openapi.json lists `method path` (or has no schema to check against).

    A missing route is re-checked for `grace` seconds: right after a file is written, a
    `--reload` dev server may still be serving the previous version of the app.
    """
    deadline = time.monotonic() + grace
    while True:
        try:
            with urllib.request.urlopen(f"{base}/openapi.json", timeout=URL_TIMEOUT) as response:
                spec = json.loads(response.read().decode("utf-8", "replace"))
        except Exception:
            if time.monotonic() >= deadline:
                return None  # not a FastAPI/OpenAPI app: the request itself is the check
            time.sleep(0.5)  # mid-reload the server briefly refuses connections
            continue
        paths = spec.get("paths") or {}
        if not isinstance(paths, dict):
            return None
        operation = paths.get(path) or {}
        if method.lower() in operation:
            return None
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    available = [f"{m.upper()} {p}" for p, ops in paths.items() if isinstance(ops, dict)
                 for m in ops if m.lower() in ("get", "post", "put", "patch", "delete")]
    return (f"the running app has no such endpoint. Its docs list: {', '.join(available) or 'nothing'}. "
            "Use one of those exactly (path and method).")


def encode_multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes]]) -> tuple[bytes, str]:
    """multipart/form-data body for form fields plus (field -> (file name, content)) uploads."""
    import mimetypes
    import uuid

    boundary = f"----tutorialai{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    for name, (file_name, content) in files.items():
        content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{file_name}\"\r\n"
            f"Content-Type: {content_type}\r\n\r\n".encode() + content + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class _OutputReader:
    """Continuously drains a process's stdout on a thread.

    A server that nobody reads from blocks as soon as the pipe buffer fills (~64 KB of reload and
    request logs), which looks like a hung server. Keeping the last lines is enough for diagnostics.
    """

    def __init__(self, process: subprocess.Popen, keep_lines: int = 400) -> None:
        import collections
        import threading

        self._head: list[str] = []  # the first lines (start-up messages live here)
        self._tail: collections.deque[str] = collections.deque(maxlen=keep_lines)
        self._keep = keep_lines
        self._lock = threading.Lock()
        self.last_line_at = time.monotonic()
        assert process.stdout is not None
        self._thread = threading.Thread(target=self._pump, args=(process.stdout,), daemon=True)
        self._thread.start()

    def _pump(self, stream) -> None:
        for line in iter(stream.readline, ""):
            with self._lock:
                if len(self._head) < self._keep:
                    self._head.append(line)
                else:
                    self._tail.append(line)
                self.last_line_at = time.monotonic()

    def text(self) -> str:
        with self._lock:
            if self._tail:
                return "".join(self._head) + "...\n" + "".join(self._tail)
            return "".join(self._head)


def _wait_for_server(process: subprocess.Popen, reader: "_OutputReader", expect_output: str | None,
                     timeout: float) -> tuple[bool, str]:
    """Wait until `expect_output` appears in the output (or, without one, until the process has
    been quiet for a few seconds without exiting)."""
    started = time.monotonic()
    deadline = started + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            time.sleep(0.2)  # let the reader collect the last lines
            return False, reader.text()
        output = reader.text()
        if expect_output and expect_output in output:
            return True, output
        if not expect_output and time.monotonic() - reader.last_line_at > 3 and time.monotonic() - started > 4:
            return True, output
        time.sleep(0.25)
    return (expect_output is None and process.poll() is None), reader.text()


def _terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=5)
    except (subprocess.TimeoutExpired, ProcessLookupError):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
