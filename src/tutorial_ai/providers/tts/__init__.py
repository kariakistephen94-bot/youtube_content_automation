from tutorial_ai.config import Settings
from tutorial_ai.providers.tts.base import AudioResult, TTSError, TTSProvider


def create_tts_provider(settings: Settings, provider: str | None = None, voice: str | None = None) -> TTSProvider:
    """Build the configured TTS provider (or an explicit one). The only place providers are chosen."""
    name = provider or settings.tts_provider
    if name == "openai-tts":
        if settings.openai_api_key is None:
            raise TTSError("OPENAI_API_KEY is not set; needed for the openai-tts provider.")
        from tutorial_ai.providers.tts.openai_tts import DEFAULT_VOICE, OpenAITTSProvider

        return OpenAITTSProvider(settings.openai_api_key.get_secret_value(), settings.openai_tts_model,
                                 voice or settings.tts_voice or DEFAULT_VOICE)
    if name == "macos-say":
        from tutorial_ai.providers.tts.macos_say import DEFAULT_VOICE, MacOSSayProvider

        return MacOSSayProvider(voice or settings.tts_voice or DEFAULT_VOICE, settings.tts_words_per_minute)
    if name == "gemini-tts":
        if settings.gemini_api_key is None:
            raise TTSError("GEMINI_API_KEY is not set; needed for the gemini-tts provider.")
        from tutorial_ai.providers.tts.gemini_tts import DEFAULT_VOICE, GeminiTTSProvider

        return GeminiTTSProvider(
            settings.gemini_api_key.get_secret_value(), settings.gemini_tts_model,
            voice or settings.tts_voice or DEFAULT_VOICE, vertexai=settings.gemini_use_vertexai,
        )
    raise TTSError(f"Unknown TTS provider {name!r}. Supported: openai-tts, macos-say, gemini-tts")


__all__ = ["AudioResult", "TTSError", "TTSProvider", "create_tts_provider"]
