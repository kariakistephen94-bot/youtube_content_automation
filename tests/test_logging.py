import logging

from tutorial_ai.utils.logging import SecretRedactingFilter
from tutorial_ai.utils.text import slugify


def _record(message: str, *args) -> logging.LogRecord:
    return logging.LogRecord("t", logging.INFO, __file__, 1, message, args, None)


def test_registered_secrets_are_redacted():
    record = _record("using key %s", "my-very-secret-key")
    SecretRedactingFilter(["my-very-secret-key"]).filter(record)
    assert "my-very-secret-key" not in record.getMessage()


def test_google_keys_and_query_params_are_redacted_even_if_unregistered():
    f = SecretRedactingFilter()
    text = f.redact("GET https://x/y?key=abc123&api_key=zzz AIza" + "A" * 35)
    assert "zzz" not in text and "AIza" + "A" * 35 not in text


def test_slugify():
    assert slugify("FastAPI File-Upload!") == "fastapi-file-upload"
    assert slugify("../../etc") == "etc"
    assert slugify("!!!") == "project"
