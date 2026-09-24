from tutorial_ai.config import Settings
from tutorial_ai.providers.ai.base import AIProvider, AIProviderError, AIResponseValidationError


class ConfigurationError(RuntimeError):
    pass


def create_ai_provider(settings: Settings) -> AIProvider:
    """Build the configured AI provider. This is the only place provider classes are chosen."""
    if settings.ai_provider == "openai":
        if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value():
            raise ConfigurationError(
                "OPENAI_API_KEY is not set. Add it to .env.local (OPENAI_API_KEY=sk-...) or set "
                "AI_PROVIDER=gemini to use Gemini."
            )
        from tutorial_ai.providers.ai.openai_provider import OpenAIProvider

        return OpenAIProvider(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.openai_model,
            temperature=settings.ai_temperature,
            max_output_tokens=settings.ai_max_output_tokens,
            max_validation_retries=settings.ai_max_validation_retries,
            timeout_seconds=settings.ai_request_timeout_seconds,
        )
    if settings.ai_provider == "gemini":
        if settings.gemini_api_key is None or not settings.gemini_api_key.get_secret_value():
            raise ConfigurationError(
                "GEMINI_API_KEY is not set. Add it to .env (see .env.example) or export it. "
                "Get a key at https://aistudio.google.com/apikey"
            )
        from tutorial_ai.providers.ai.gemini import GeminiProvider

        return GeminiProvider(
            api_key=settings.gemini_api_key.get_secret_value(),
            model=settings.gemini_model,
            temperature=settings.ai_temperature,
            max_output_tokens=settings.ai_max_output_tokens,
            max_validation_retries=settings.ai_max_validation_retries,
            timeout_seconds=settings.ai_request_timeout_seconds,
            vertexai=settings.gemini_use_vertexai,
        )
    raise ConfigurationError(f"Unknown AI provider {settings.ai_provider!r}. Supported: openai, gemini")


__all__ = [
    "AIProvider",
    "AIProviderError",
    "AIResponseValidationError",
    "ConfigurationError",
    "create_ai_provider",
]
