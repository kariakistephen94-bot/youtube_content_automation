from pathlib import Path

import pytest
from pydantic import ValidationError

from tutorial_ai.config import DEFAULT_GEMINI_MODEL, Settings
from tutorial_ai.providers.ai import ConfigurationError, create_ai_provider


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("GEMINI_API_KEY", "GEMINI_MODEL", "TUTORIAL_AI_MAX_CONTEXT_TOKENS", "GOOGLE_GENAI_USE_VERTEXAI",
                "OPENAI_API_KEY", "OPEN_AI_LISBON_API_KEY", "AI_PROVIDER", "TTS_PROVIDER"):
        monkeypatch.delenv(key, raising=False)


def test_defaults():
    settings = Settings(_env_file=None)
    assert settings.gemini_model == DEFAULT_GEMINI_MODEL
    assert settings.gemini_api_key is None
    assert settings.output_directory == Path("output")


def test_reads_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTESTKEY1234567890")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    monkeypatch.setenv("TUTORIAL_AI_MAX_CONTEXT_TOKENS", "50000")
    settings = Settings(_env_file=None)
    assert settings.gemini_api_key.get_secret_value() == "AIzaTESTKEY1234567890"
    assert settings.gemini_model == "gemini-test"
    assert settings.max_context_tokens == 50_000


def test_reads_env_file(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=from-file-key-123\nGEMINI_MODEL=from-file-model\n")
    settings = Settings(_env_file=env)
    assert settings.gemini_model == "from-file-model"


def test_api_key_is_not_exposed_in_repr(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTESTKEY1234567890")
    settings = Settings(_env_file=None)
    assert "AIzaTESTKEY1234567890" not in repr(settings)
    assert "AIzaTESTKEY1234567890" not in settings.model_dump_json()


def test_invalid_values_rejected(monkeypatch):
    monkeypatch.setenv("TUTORIAL_AI_MAX_CONTEXT_TOKENS", "10")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_missing_api_key_gives_clear_error():
    with pytest.raises(ConfigurationError, match="GEMINI_API_KEY is not set"):
        create_ai_provider(Settings(_env_file=None, ai_provider="gemini"))


def test_unknown_provider():
    with pytest.raises(ConfigurationError, match="Unknown AI provider"):
        create_ai_provider(Settings(_env_file=None, ai_provider="nope"))


def test_creates_gemini_provider_without_network(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaTESTKEY1234567890")
    provider = create_ai_provider(Settings(_env_file=None, ai_provider="gemini"))
    assert provider.name == "gemini"
    assert provider.model == DEFAULT_GEMINI_MODEL


def test_vertexai_only_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AQ.Ab8" + "x" * 40)  # AI Studio keys can look like this
    assert Settings(_env_file=None).gemini_use_vertexai is False
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    assert Settings(_env_file=None).gemini_use_vertexai is True
