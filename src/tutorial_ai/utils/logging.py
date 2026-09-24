import logging
import re
from pathlib import Path

LOGGER_NAME = "tutorial_ai"

# Catches API keys that end up in messages even if they were never registered explicitly
# (e.g. inside an exception string from an HTTP client).
_KEY_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}|AQ\.[0-9A-Za-z_\-]{30,}|sk-[0-9A-Za-z_\-]{20,}"),  # Google / OpenAI keys
    re.compile(r"(?i)(api[_-]?key|token|secret)=([^&\s]+)"),
]


class SecretRedactingFilter(logging.Filter):
    def __init__(self, secrets: list[str] | None = None) -> None:
        super().__init__()
        self._secrets = [s for s in (secrets or []) if s and len(s) >= 8]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = self.redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***REDACTED***")
        text = _KEY_PATTERNS[0].sub("***REDACTED***", text)
        text = _KEY_PATTERNS[1].sub(r"\1=***REDACTED***", text)
        return text


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def setup_logging(level: str = "INFO", secrets: list[str] | None = None) -> None:
    root = logging.getLogger(LOGGER_NAME)
    root.handlers.clear()
    root.setLevel(logging.DEBUG)
    root.propagate = False

    # Plain one-line format: stays readable in narrow terminals (Rich's column layout wrapped
    # every message into a thin column).
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    console.setLevel(level.upper())
    console.addFilter(SecretRedactingFilter(secrets))
    root.addHandler(console)

    for noisy in ("httpx", "httpcore", "google_genai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def add_file_logging(log_file: Path, secrets: list[str] | None = None) -> None:
    """Log everything (DEBUG and up) for this run to a file inside the project output."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    handler.addFilter(SecretRedactingFilter(secrets))
    logging.getLogger(LOGGER_NAME).addHandler(handler)
