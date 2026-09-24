"""Background jobs for the web API.

Only one job runs at a time: pipeline steps share the AI quota, and desktop automation shares the
keyboard. Each job's log lines are captured (secrets redacted) so the UI can stream them.
"""

import logging
import re
import threading
import traceback
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from tutorial_ai.utils.logging import LOGGER_NAME, SecretRedactingFilter, get_logger

log = get_logger("api.jobs")
MAX_JOBS_KEPT = 50


class JobStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Job(BaseModel):
    id: str
    kind: str
    project: str | None = None
    status: JobStatus = JobStatus.RUNNING
    created_at: datetime
    finished_at: datetime | None = None
    logs: list[str] = Field(default_factory=list)
    progress: float | None = Field(default=None, ge=0, le=1, description="0..1 when known")
    progress_label: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None

    def note(self, message: str) -> None:
        """Record a log line and derive progress from 'k/n' or 'section k/n' style messages."""
        self.logs.append(message)
        match = _PROGRESS.search(message)
        if match:
            done, total = int(match.group(1)), int(match.group(2))
            if total > 0 and done <= total:
                # "Writing section 3/7" means 2 are done; "[3/30] clip" means 3 are done.
                completed = done - 1 if message.lower().startswith(("writing", "recording", "section")) or "writing section" in message.lower() else done
                self.progress = max(0.0, min(completed / total, 0.99))
                self.progress_label = f"{message.split(':', 1)[0][:60]}"


_PROGRESS = re.compile(r"(?:\[|\b)(\d+)\s*/\s*(\d+)(?:\]|\b)")


class JobBusyError(RuntimeError):
    pass


class _JobLogHandler(logging.Handler):
    """Routes log records from a job's thread into that job's log."""

    def __init__(self, manager: "JobManager") -> None:
        super().__init__(level=logging.INFO)
        self._manager = manager
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        job = self._manager.job_for_thread(record.thread)
        if job is not None:
            job.note(self.format(record))


class JobManager:
    def __init__(self, secrets: Callable[[], list[str]] = list) -> None:
        self._jobs: dict[str, Job] = {}
        self._threads: dict[int, str] = {}
        self._lock = threading.Lock()
        self._secrets = secrets
        handler = _JobLogHandler(self)
        handler.addFilter(_DynamicRedactor(secrets))
        logger = logging.getLogger(LOGGER_NAME)
        logger.addHandler(handler)
        if logger.getEffectiveLevel() > logging.INFO:
            logger.setLevel(logging.INFO)  # INFO lines are the job's progress log
        self._handler = handler

    def close(self) -> None:
        logging.getLogger(LOGGER_NAME).removeHandler(self._handler)

    def job_for_thread(self, thread_id: int | None) -> Job | None:
        job_id = self._threads.get(thread_id) if thread_id is not None else None
        return self._jobs.get(job_id) if job_id else None

    @property
    def running(self) -> Job | None:
        return next((j for j in self._jobs.values() if j.status is JobStatus.RUNNING), None)

    def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def submit(
        self, kind: str, work: Callable[[Callable[[str], None]], dict | None],
        project: str | None = None,
    ) -> Job:
        """Start `work(step)` in a thread. `step(message)` adds a line to the job log."""
        with self._lock:
            if self.running is not None:
                raise JobBusyError(f"Another job is running ({self.running.kind}). Wait for it to finish.")
            job = Job(id=uuid.uuid4().hex[:12], kind=kind, project=project,
                      created_at=datetime.now(UTC))
            self._jobs[job.id] = job
            self._trim()
        thread = threading.Thread(target=self._run, args=(job, work), daemon=True)
        thread.start()
        return job

    def _run(self, job: Job, work: Callable[[Callable[[str], None]], dict | None]) -> None:
        self._threads[threading.get_ident()] = job.id
        redactor = SecretRedactingFilter(self._secrets())

        def step(message: str) -> None:
            job.note(redactor.redact(message))

        try:
            job.result = work(step)
            job.status = JobStatus.SUCCEEDED
            job.progress = 1.0
        except Exception as exc:  # the job's failure is reported to the UI, not raised
            job.error = redactor.redact(str(exc))
            job.status = JobStatus.FAILED
            log.debug("Job %s failed:\n%s", job.id, traceback.format_exc())
            job.logs.append(f"ERROR   {job.error}")
        finally:
            job.finished_at = datetime.now(UTC)
            self._threads.pop(threading.get_ident(), None)

    def _trim(self) -> None:
        finished = [j for j in self.list() if j.status is not JobStatus.RUNNING]
        for old in finished[MAX_JOBS_KEPT:]:
            self._jobs.pop(old.id, None)


class _DynamicRedactor(logging.Filter):
    """Like SecretRedactingFilter, but re-reads the secrets (e.g. a token saved after startup)."""

    def __init__(self, secrets: Callable[[], list[str]]) -> None:
        super().__init__()
        self._secrets = secrets

    def filter(self, record: logging.LogRecord) -> bool:
        return SecretRedactingFilter(self._secrets()).filter(record)
