"""OpenAI implementation of AIProvider (Chat Completions with JSON-schema output)."""

import time

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

from tutorial_ai.models.repository import RepositoryAnalysis, RepositoryInsights
from tutorial_ai.models.script import ScriptSectionDraft, ScriptSectionRequest
from tutorial_ai.models.tutorial import TutorialPlan, TutorialRequest
from tutorial_ai.providers.ai import prompts
from tutorial_ai.providers.ai.base import AIProvider, AIProviderError, AIResponseValidationError
from tutorial_ai.providers.ai.gemini import _format_validation_errors, parse_structured_response
from tutorial_ai.utils.logging import get_logger

log = get_logger("providers.ai.openai")

MAX_NETWORK_RETRIES = 3


class OpenAIProvider(AIProvider):
    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        temperature: float = 0.3,
        max_output_tokens: int = 32_768,
        max_validation_retries: int = 2,
        timeout_seconds: float = 300.0,
        client: OpenAI | None = None,
    ) -> None:
        self.model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._max_validation_retries = max_validation_retries
        # The SDK retries 429/5xx itself with backoff; we add retries for dropped connections.
        self._client = client or OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=4)
        self._sleep = time.sleep

    def analyze_repository(self, repository_context: str) -> RepositoryInsights:
        return self._generate_structured(
            "analyze_repository", prompts.REPOSITORY_ANALYSIS_SYSTEM,
            prompts.repository_analysis_prompt(repository_context), RepositoryInsights,
        )

    def create_tutorial_plan(self, analysis: RepositoryAnalysis, request: TutorialRequest) -> TutorialPlan:
        return self._generate_structured(
            "create_tutorial_plan", prompts.PLAN_SYSTEM,
            prompts.build_tutorial_plan_prompt(analysis, request), TutorialPlan,
        )

    def create_script_section(self, request: ScriptSectionRequest) -> ScriptSectionDraft:
        return self._generate_structured(
            f"create_script_section[{request.section_number}]", prompts.SCRIPT_SYSTEM,
            prompts.build_script_section_prompt(request), ScriptSectionDraft,
        )

    def _generate_structured[T: BaseModel](
        self, operation: str, system_instruction: str, prompt: str, schema: type[T]
    ) -> T:
        messages: list[dict] = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ]
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": False},
        }
        attempts = 1 + self._max_validation_retries
        last_error = ""
        for attempt in range(1, attempts + 1):
            text = self._call(operation, messages, response_format, attempt)
            try:
                return parse_structured_response(text, schema)
            except ValidationError as exc:
                last_error = _format_validation_errors(exc)
                log.warning("%s: response failed schema validation (attempt %d/%d): %s",
                            operation, attempt, attempts, last_error)
                messages += [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": prompts.validation_retry_prompt(last_error)},
                ]
        raise AIResponseValidationError(
            f"{operation}: OpenAI returned invalid output {attempts} times. Last errors: {last_error}"
        )

    def _call(self, operation: str, messages: list[dict], response_format: dict, attempt: int) -> str:
        log.info("%s: calling OpenAI model %s (attempt %d)", operation, self.model, attempt)
        started = time.monotonic()
        network_retries = 0
        while True:
            try:
                kwargs = dict(model=self.model, messages=messages, response_format=response_format,
                              max_completion_tokens=self._max_output_tokens)
                if not _is_reasoning_model(self.model):
                    kwargs["temperature"] = self._temperature
                response = self._client.chat.completions.create(**kwargs)
                break
            except (APIConnectionError, APITimeoutError, httpx.HTTPError) as exc:
                network_retries += 1
                if network_retries > MAX_NETWORK_RETRIES:
                    raise AIProviderError(
                        f"{operation}: lost the connection to OpenAI {network_retries} times "
                        f"({type(exc).__name__}). Check your internet connection and re-run; completed "
                        "steps are kept."
                    ) from exc
                delay = 2.0 * network_retries
                log.warning("%s: connection problem (%s); retrying in %.0fs", operation, type(exc).__name__, delay)
                self._sleep(delay)
            except RateLimitError as exc:
                raise AIProviderError(
                    f"{operation}: OpenAI rate limit or quota exceeded (429): {_message(exc)} "
                    "Check the usage/billing page for this key and re-run; completed steps are kept."
                ) from exc
            except APIStatusError as exc:
                raise AIProviderError(_describe(exc, self.model)) from exc

        elapsed = time.monotonic() - started
        usage = response.usage
        if usage:
            log.info("%s: done in %.1fs - prompt %s tokens, output %s tokens", operation, elapsed,
                     usage.prompt_tokens, usage.completion_tokens)
        choice = response.choices[0] if response.choices else None
        text = (choice.message.content or "") if choice else ""
        if not text:
            reason = choice.finish_reason if choice else None
            refusal = getattr(choice.message, "refusal", None) if choice else None
            raise AIProviderError(f"{operation}: OpenAI returned no text (finish_reason={reason}, refusal={refusal})")
        if choice and choice.finish_reason == "length":
            log.warning("%s: output hit the token limit and is probably truncated", operation)
        return text


def _is_reasoning_model(model: str) -> bool:
    return model.startswith(("o1", "o3", "o4", "gpt-5"))


def _message(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
    return str(exc)


def _describe(exc: APIStatusError, model: str) -> str:
    code = exc.status_code
    message = _message(exc)
    if code in (401, 403):
        return f"OpenAI rejected the API key ({code}): {message}"
    if code == 404:
        return f"OpenAI model {model!r} was not found ({code}). Pick another model. {message}"
    return f"OpenAI API error ({code}): {message}"


def list_openai_models(api_key: str) -> list[str]:
    """Chat-capable model ids for the model picker (best effort filter)."""
    client = OpenAI(api_key=api_key, timeout=30)
    try:
        ids = sorted(m.id for m in client.models.list())
    except APIStatusError as exc:
        raise AIProviderError(_describe(exc, "")) from exc
    skip = ("realtime", "audio", "transcribe", "tts", "image", "embedding", "moderation", "whisper",
            "dall-e", "search", "instruct", "davinci", "babbage", "codex")
    return [m for m in ids if m.startswith(("gpt-", "o1", "o3", "o4")) and not any(s in m for s in skip)]
