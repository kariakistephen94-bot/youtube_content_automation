from types import SimpleNamespace

import httpx
import pytest
from conftest import make_insights, make_plan, make_sections
from openai import APIStatusError, RateLimitError

from tutorial_ai.config import Settings, ai_model, with_ai_model
from tutorial_ai.models.script import ScriptSectionRequest
from tutorial_ai.providers.ai import ConfigurationError, create_ai_provider
from tutorial_ai.providers.ai.base import AIProviderError, AIResponseValidationError
from tutorial_ai.providers.ai.openai_provider import OpenAIProvider


def _response(text, finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text, refusal=None), finish_reason=finish)],
                           usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))


class FakeChat:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _provider(outcomes, model="gpt-5.4-mini"):
    chat = FakeChat(outcomes)
    client = SimpleNamespace(chat=SimpleNamespace(completions=chat))
    return OpenAIProvider("k", model, client=client), chat


def _status_error(code):
    return APIStatusError("boom", response=httpx.Response(code, request=httpx.Request("POST", "https://x")), body={"error": {"message": "boom"}})


def test_structured_output_request_and_parse():
    provider, chat = _provider([_response(make_insights().model_dump_json())])
    assert provider.analyze_repository("ctx").primary_language == "Python"
    kwargs = chat.calls[0]
    assert kwargs["response_format"]["json_schema"]["name"] == "RepositoryInsights"
    assert kwargs["messages"][0]["role"] == "system" and "ctx" in kwargs["messages"][1]["content"]
    assert "temperature" not in kwargs  # reasoning models reject it


def test_temperature_sent_for_non_reasoning_models():
    provider, chat = _provider([_response(make_insights().model_dump_json())], model="gpt-4.1")
    provider.analyze_repository("ctx")
    assert chat.calls[0]["temperature"] == 0.3


def test_validation_retry_then_success():
    provider, chat = _provider([_response('{"description": "x"}'), _response(make_plan().model_dump_json())])
    plan = provider.create_tutorial_plan(_analysis(), __import__("tutorial_ai.models.tutorial", fromlist=["TutorialRequest"]).TutorialRequest())
    assert plan.title == make_plan().title
    assert chat.calls[1]["messages"][-1]["role"] == "user" and "schema" in chat.calls[1]["messages"][-1]["content"]


def test_gives_up_after_retries():
    provider, _ = _provider([_response("nope")] * 3)
    with pytest.raises(AIResponseValidationError, match="3 times"):
        provider.create_script_section(ScriptSectionRequest(plan=make_plan(), section_number=1, repository_summary="r"))


def test_errors_are_translated():
    provider, _ = _provider([_status_error(401)])
    with pytest.raises(AIProviderError, match="rejected the API key"):
        provider.analyze_repository("ctx")
    provider, _ = _provider([RateLimitError("rl", response=httpx.Response(429, request=httpx.Request("POST", "https://x")), body=None)])
    with pytest.raises(AIProviderError, match="429"):
        provider.analyze_repository("ctx")
    provider, _ = _provider([httpx.ConnectError("down")] * 5)
    provider._sleep = lambda s: None
    with pytest.raises(AIProviderError, match="lost the connection"):
        provider.analyze_repository("ctx")


def test_factory_and_model_helpers(monkeypatch):
    for key in ("OPENAI_API_KEY", "OPEN_AI_LISBON_API_KEY", "AI_PROVIDER", "GEMINI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.ai_provider == "openai"
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        create_ai_provider(settings)
    monkeypatch.setenv("OPEN_AI_LISBON_API_KEY", "sk-test-1234567890abcdefghij")
    settings = Settings(_env_file=None)
    provider = create_ai_provider(settings)
    assert provider.name == "openai" and provider.model == ai_model(settings) == "gpt-5.4-mini"
    assert ai_model(with_ai_model(settings, "gpt-4.1")) == "gpt-4.1"
    assert create_ai_provider(Settings(_env_file=None, ai_provider="gemini", gemini_api_key="AQ.x" * 12)).name == "gemini"


def test_openai_tts_writes_wav(tmp_path):
    import wave

    from tutorial_ai.providers.tts.openai_tts import OpenAITTSProvider

    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        import io
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b"\0\0" * 12000)
        return SimpleNamespace(content=buf.getvalue())
    client = SimpleNamespace(audio=SimpleNamespace(speech=SimpleNamespace(create=create)))
    result = OpenAITTSProvider("k", client=client).synthesize("Hello there.", tmp_path / "o.wav")
    assert result.duration_seconds == pytest.approx(0.5) and calls[0]["response_format"] == "wav"
    assert "instructions" in calls[0]


def _analysis():
    from tutorial_ai.models.repository import RepositoryAnalysis

    return RepositoryAnalysis.model_validate({
        **make_insights().model_dump(), "repository_name": "r",
        "source": {"name": "r", "kind": "local", "origin": ".", "local_path": "."},
        "analyzed_at": "2026-01-01T00:00:00Z", "ai_model": "x", "languages": [], "project_structure": "main.py",
        "files_to_ignore": [], "scan_summary": {"total_files": 1, "ignored_files": 0, "files_with_content_sent": 1,
                                                "files_truncated": 0, "files_omitted_for_budget": 0, "estimated_context_tokens": 10},
    })
