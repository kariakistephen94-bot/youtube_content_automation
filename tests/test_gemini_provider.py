"""GeminiProvider tests using a fake client - no network, no real API calls."""

from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors as genai_errors
from google.genai import types

from tutorial_ai.providers.ai.base import AIProviderError, AIResponseValidationError
from tutorial_ai.providers.ai.gemini import GeminiProvider, parse_structured_response
from tutorial_ai.models.repository import RepositoryAnalysis, RepositoryInsights
from tutorial_ai.models.script import ScriptSectionDraft, ScriptSectionRequest
from tutorial_ai.models.tutorial import TutorialPlan, TutorialRequest

from conftest import make_insights, make_plan, make_sections


def _response(text: str | None, finish_reason=types.FinishReason.STOP) -> types.GenerateContentResponse:
    parts = [types.Part(text=text)] if text is not None else []
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(content=types.Content(role="model", parts=parts), finish_reason=finish_reason)
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=100, candidates_token_count=50
        ),
    )


class FakeModels:
    def __init__(self, outcomes: list) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": list(contents), "config": config})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _provider(outcomes: list, retries: int = 2) -> tuple[GeminiProvider, FakeModels]:
    models = FakeModels(outcomes)
    client = SimpleNamespace(models=models)
    provider = GeminiProvider(
        api_key="unused", model="gemini-test", max_validation_retries=retries, client=client
    )
    return provider, models


VALID_JSON = make_insights().model_dump_json()


def test_valid_response_is_parsed_and_request_is_structured():
    provider, models = _provider([_response(VALID_JSON)])
    insights = provider.analyze_repository("# Repository: x")
    assert insights.primary_language == "Python"

    call = models.calls[0]
    assert call["model"] == "gemini-test"
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_json_schema == RepositoryInsights.model_json_schema()
    assert "# Repository: x" in call["contents"][0].parts[0].text


def test_invalid_response_is_retried_with_validation_errors():
    provider, models = _provider([_response('{"description": "only this"}'), _response(VALID_JSON)])
    insights = provider.analyze_repository("ctx")
    assert insights.description == "A FastAPI upload API."
    assert len(models.calls) == 2
    retry_contents = models.calls[1]["contents"]
    assert [c.role for c in retry_contents] == ["user", "model", "user"]
    assert "primary_language" in retry_contents[2].parts[0].text  # the missing field is reported


def test_gives_up_after_max_retries():
    provider, models = _provider([_response("not json")] * 3, retries=2)
    with pytest.raises(AIResponseValidationError, match="3 times"):
        provider.analyze_repository("ctx")
    assert len(models.calls) == 3


def test_empty_response_raises():
    provider, _ = _provider([_response(None, finish_reason=types.FinishReason.SAFETY)])
    with pytest.raises(AIProviderError, match="no text"):
        provider.analyze_repository("ctx")


@pytest.mark.parametrize(
    ("code", "message"),
    [(401, "rejected the API key"), (404, "was not found"), (429, "rate limit"), (500, "API error")],
)
def test_api_errors_are_translated(code, message):
    error = genai_errors.APIError(
        code, {"error": {"message": "boom", "status": "X"}}, httpx.Response(code)
    )
    provider, _ = _provider([error])
    with pytest.raises(AIProviderError, match=message):
        provider.analyze_repository("ctx")


def test_code_fenced_json_is_accepted():
    fenced = f"```json\n{VALID_JSON}\n```"
    assert parse_structured_response(fenced, RepositoryInsights).primary_language == "Python"


def test_plan_and_script_calls_use_their_schemas():
    plan = make_plan()
    provider, models = _provider([
        _response(plan.model_dump_json()),
        _response(make_sections()[1].model_dump_json()),
    ])
    analysis = RepositoryAnalysis.model_validate({
        **make_insights().model_dump(), "repository_name": "r",
        "source": {"name": "r", "kind": "local", "origin": ".", "local_path": "."},
        "analyzed_at": "2026-01-01T00:00:00Z", "ai_model": "x", "languages": [],
        "project_structure": "main.py", "files_to_ignore": [],
        "scan_summary": {"total_files": 1, "ignored_files": 0, "files_with_content_sent": 1,
                         "files_truncated": 0, "files_omitted_for_budget": 0,
                         "estimated_context_tokens": 10},
    })
    assert provider.create_tutorial_plan(analysis, TutorialRequest()) == plan
    draft = provider.create_script_section(
        ScriptSectionRequest(plan=plan, section_number=1, repository_summary="r")
    )
    assert draft.beats[0].kind.value == "talk"
    assert models.calls[0]["config"].response_json_schema == TutorialPlan.model_json_schema()
    assert models.calls[1]["config"].response_json_schema == ScriptSectionDraft.model_json_schema()
    assert "Teaching style" in models.calls[1]["config"].system_instruction


def test_banned_phrase_triggers_a_rewrite_request():
    bad = '{"beats": [{"kind": "talk", "narration": "Let us delve into FastAPI."}], "state_summary": "s"}'
    good = '{"beats": [{"kind": "talk", "narration": "Alright, let us look at FastAPI."}], "state_summary": "s"}'
    provider, models = _provider([_response(bad), _response(good)])
    draft = provider.create_script_section(
        ScriptSectionRequest(plan=make_plan(), section_number=1, repository_summary="r")
    )
    assert draft.beats[0].narration.startswith("Alright")
    assert "sounds machine-written" in models.calls[1]["contents"][-1].parts[0].text


def _rate_limit_error(delay: str = "12.5s") -> genai_errors.APIError:
    message = f"You exceeded your current quota. Please retry in {delay}."
    return genai_errors.APIError(
        429, {"error": {"message": message, "status": "RESOURCE_EXHAUSTED"}}, httpx.Response(429)
    )


def test_rate_limit_waits_for_requested_delay_then_succeeds():
    provider, models = _provider([_rate_limit_error("12.5s"), _response(VALID_JSON)])
    slept = []
    provider._sleep = slept.append
    assert provider.analyze_repository("ctx").primary_language == "Python"
    assert slept == [13.5]
    assert len(models.calls) == 2


def test_rate_limit_gives_up_after_max_waits_or_long_delays():
    provider, _ = _provider([_rate_limit_error()] * 5)
    provider._sleep = lambda s: None
    with pytest.raises(AIProviderError, match="Free-tier"):
        provider.analyze_repository("ctx")

    provider, models = _provider([_rate_limit_error("3600s")])  # daily quota: don't wait an hour
    with pytest.raises(AIProviderError, match="429"):
        provider.analyze_repository("ctx")
    assert len(models.calls) == 1


def test_dropped_connections_are_retried_then_reported():
    provider, models = _provider([httpx.RemoteProtocolError("Server disconnected"), _response(VALID_JSON)])
    slept = []
    provider._sleep = slept.append
    assert provider.analyze_repository("ctx").primary_language == "Python"
    assert slept == [2.0] and len(models.calls) == 2

    provider, _ = _provider([httpx.ConnectError("no route")] * 6)
    provider._sleep = lambda s: None
    with pytest.raises(AIProviderError, match="lost the connection"):
        provider.analyze_repository("ctx")
