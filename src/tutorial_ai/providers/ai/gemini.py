"""Google Gemini implementation of AIProvider (google-genai SDK)."""

import re
import time

import httpx

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ValidationError

from tutorial_ai.models.repository import RepositoryAnalysis, RepositoryInsights
from tutorial_ai.models.script import ScriptSectionDraft, ScriptSectionRequest
from tutorial_ai.models.tutorial import TutorialPlan, TutorialRequest
from tutorial_ai.providers.ai import prompts
from tutorial_ai.providers.ai.base import AIProvider, AIProviderError, AIResponseValidationError
from tutorial_ai.utils.logging import get_logger

log = get_logger("providers.ai.gemini")

# Transient HTTP statuses the SDK retries itself, with exponential backoff. 429 is excluded: quick
# retries only burn quota; we wait for the delay the API asks for instead (see _call).
_RETRY_STATUSES = [408, 500, 502, 503, 504]
_RETRY_AFTER = re.compile(r"retry in ([\d.]+)\s*s", re.IGNORECASE)
MAX_RATE_LIMIT_WAITS = 3
MAX_NETWORK_RETRIES = 3
MAX_RATE_LIMIT_WAIT_SECONDS = 120.0
_CODE_FENCE = re.compile(r"^\s*```(?:json)?\s*\n(.*)\n\s*```\s*$", re.DOTALL)


class GeminiProvider(AIProvider):
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str,
        temperature: float = 0.3,
        max_output_tokens: int = 32_768,
        max_validation_retries: int = 2,
        timeout_seconds: float = 300.0,
        vertexai: bool = False,
        client: genai.Client | None = None,
    ) -> None:
        self.model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._max_validation_retries = max_validation_retries
        self._vertexai = vertexai
        self._sleep = time.sleep  # replaced in tests
        self._client = client or genai.Client(
            api_key=api_key,
            vertexai=vertexai,
            http_options=types.HttpOptions(
                timeout=int(timeout_seconds * 1000),
                retry_options=types.HttpRetryOptions(
                    attempts=6, initial_delay=2.0, max_delay=60.0,
                    http_status_codes=_RETRY_STATUSES,
                ),
            ),
        )

    def analyze_repository(self, repository_context: str) -> RepositoryInsights:
        return self._generate_structured(
            operation="analyze_repository",
            system_instruction=prompts.REPOSITORY_ANALYSIS_SYSTEM,
            prompt=prompts.repository_analysis_prompt(repository_context),
            schema=RepositoryInsights,
        )

    def create_tutorial_plan(
        self, analysis: RepositoryAnalysis, request: TutorialRequest
    ) -> TutorialPlan:
        return self._generate_structured(
            operation="create_tutorial_plan",
            system_instruction=prompts.PLAN_SYSTEM,
            prompt=prompts.build_tutorial_plan_prompt(analysis, request),
            schema=TutorialPlan,
        )

    def create_script_section(self, request: ScriptSectionRequest) -> ScriptSectionDraft:
        return self._generate_structured(
            operation=f"create_script_section[{request.section_number}]",
            system_instruction=prompts.SCRIPT_SYSTEM,
            prompt=prompts.build_script_section_prompt(request),
            schema=ScriptSectionDraft,
        )

    def _generate_structured[T: BaseModel](
        self, operation: str, system_instruction: str, prompt: str, schema: type[T]
    ) -> T:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=self._temperature,
            max_output_tokens=self._max_output_tokens,
            response_mime_type="application/json",
            response_json_schema=schema.model_json_schema(),
            # We never pass tools; disabling AFC also silences the SDK's AFC warning.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        contents: list[types.Content] = [
            types.Content(role="user", parts=[types.Part(text=prompt)])
        ]

        attempts = 1 + self._max_validation_retries
        last_error = ""
        for attempt in range(1, attempts + 1):
            text = self._call(operation, contents, config, attempt)
            try:
                return parse_structured_response(text, schema)
            except ValidationError as exc:
                last_error = _format_validation_errors(exc)
                log.warning(
                    "%s: response failed schema validation (attempt %d/%d): %s",
                    operation, attempt, attempts, last_error,
                )
                contents += [
                    types.Content(role="model", parts=[types.Part(text=text)]),
                    types.Content(
                        role="user",
                        parts=[types.Part(text=prompts.validation_retry_prompt(last_error))],
                    ),
                ]
        raise AIResponseValidationError(
            f"{operation}: Gemini returned invalid output {attempts} times. Last errors: {last_error}"
        )

    def _call(
        self,
        operation: str,
        contents: list[types.Content],
        config: types.GenerateContentConfig,
        attempt: int,
    ) -> str:
        log.info("%s: calling Gemini model %s (attempt %d)", operation, self.model, attempt)
        started = time.monotonic()
        waits = 0
        network_retries = 0
        while True:
            try:
                response = self._client.models.generate_content(
                    model=self.model, contents=contents, config=config
                )
                break
            except httpx.HTTPError as exc:
                # Dropped connection / read timeout: the request never got an HTTP status, so the
                # SDK's status-code retries do not apply. Retry a few times with backoff.
                network_retries += 1
                if network_retries > MAX_NETWORK_RETRIES:
                    raise AIProviderError(
                        f"{operation}: lost the connection to Gemini {network_retries} times "
                        f"({type(exc).__name__}: {exc}). Check your internet connection and re-run; "
                        "completed steps are kept."
                    ) from exc
                delay = 2.0 * network_retries
                log.warning(
                    "%s: connection problem (%s); retrying in %.0fs (%d/%d)",
                    operation, type(exc).__name__, delay, network_retries, MAX_NETWORK_RETRIES,
                )
                self._sleep(delay)
            except genai_errors.APIError as exc:
                delay = rate_limit_delay(exc)
                if delay is None or waits >= MAX_RATE_LIMIT_WAITS:
                    raise AIProviderError(
                        _describe_api_error(exc, self.model, self._vertexai)
                    ) from exc
                waits += 1
                log.warning(
                    "%s: rate limited; waiting %.0fs as requested by the API (%d/%d)",
                    operation, delay, waits, MAX_RATE_LIMIT_WAITS,
                )
                self._sleep(delay)
        elapsed = time.monotonic() - started

        usage = response.usage_metadata
        if usage is not None:
            log.info(
                "%s: done in %.1fs - prompt %s tokens, output %s tokens, thinking %s tokens",
                operation, elapsed, usage.prompt_token_count, usage.candidates_token_count,
                usage.thoughts_token_count,
            )

        text = response.text
        if not text:
            reason = None
            if response.candidates:
                reason = response.candidates[0].finish_reason
            blocked = response.prompt_feedback.block_reason if response.prompt_feedback else None
            raise AIProviderError(
                f"{operation}: Gemini returned no text (finish_reason={reason}, block_reason={blocked})"
            )
        if response.candidates and response.candidates[0].finish_reason == types.FinishReason.MAX_TOKENS:
            log.warning("%s: output hit max_output_tokens and is probably truncated", operation)
        return text


def rate_limit_delay(exc: genai_errors.APIError) -> float | None:
    """Seconds to wait before retrying a 429, if the API said how long (per-minute quotas)."""
    if getattr(exc, "code", None) != 429:
        return None
    match = _RETRY_AFTER.search(getattr(exc, "message", None) or str(exc))
    if not match:
        return None
    delay = float(match.group(1)) + 1.0
    return delay if delay <= MAX_RATE_LIMIT_WAIT_SECONDS else None


def parse_structured_response[T: BaseModel](text: str, schema: type[T]) -> T:
    """Validate model output against a schema, tolerating a surrounding ```json fence."""
    match = _CODE_FENCE.match(text)
    if match:
        text = match.group(1)
    return schema.model_validate_json(text)


def _format_validation_errors(exc: ValidationError, limit: int = 20) -> str:
    lines = []
    for error in exc.errors()[:limit]:
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.append(f"- {location}: {error['msg']}")
    if exc.error_count() > limit:
        lines.append(f"- ... and {exc.error_count() - limit} more")
    return "\n".join(lines)


def _describe_api_error(exc: genai_errors.APIError, model: str, vertexai: bool = False) -> str:
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None) or str(exc)
    endpoint = "Vertex AI" if vertexai else "Gemini API"
    if code in (401, 403) or "API key not valid" in message:
        hint = (
            "Make sure the Vertex AI API is enabled (and billing is set up) on the key's Google "
            "Cloud project, or unset GOOGLE_GENAI_USE_VERTEXAI to use an AI Studio key."
            if vertexai
            else "Check the key at https://aistudio.google.com/apikey. For a Vertex AI key, set "
            "GOOGLE_GENAI_USE_VERTEXAI=true."
        )
        return f"{endpoint} rejected the API key ({code}): {message} {hint}"
    if code == 404:
        return f"Gemini model {model!r} was not found ({code}). Check GEMINI_MODEL. {message}"
    if code == 429:
        return (
            f"Gemini rate limit or quota exceeded ({code}). {message} "
            "Free-tier keys have low per-minute and per-day request limits; wait and re-run "
            "(completed steps are kept), or enable billing on the key's project."
        )
    return f"Gemini API error ({code}): {message}"


def list_text_models(api_key: str, vertexai: bool = False) -> list[str]:
    """Model names usable for generateContent, newest-looking first."""
    client = genai.Client(api_key=api_key, vertexai=vertexai)
    try:
        names = [
            m.name.removeprefix("models/")
            for m in client.models.list()
            if "generateContent" in (m.supported_actions or [])
        ]
    except genai_errors.APIError as exc:
        raise AIProviderError(_describe_api_error(exc, "", vertexai)) from exc
    # Text models only: drop image/tts/embedding variants.
    skip = ("image", "tts", "embedding", "transcribe", "computer-use", "robotics", "omni")
    return sorted(n for n in names if "gemini" in n and not any(s in n for s in skip))
