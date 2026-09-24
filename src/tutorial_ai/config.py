"""Application configuration.

All settings come from environment variables, optionally loaded from `.env` and `.env.local`
in the current directory (`.env.local` wins; real environment variables win over both). Every setting uses the `TUTORIAL_AI_` prefix except provider credentials,
which use their conventional names (GEMINI_API_KEY, GEMINI_MODEL).
"""

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The single place default model names live. Override with GEMINI_MODEL / OPENAI_MODEL.
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
DEFAULT_OPENAI_TTS_MODEL = "gpt-4o-mini-tts"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TUTORIAL_AI_",
        env_file=(".env", ".env.local"),  # later files win: .env.local overrides .env
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- AI provider -------------------------------------------------------------------------
    ai_provider: str = Field(default="openai", validation_alias=AliasChoices("AI_PROVIDER", "TUTORIAL_AI_AI_PROVIDER"))
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "OPEN_AI_LISBON_API_KEY", "TUTORIAL_AI_OPENAI_API_KEY"),
    )
    openai_model: str = Field(
        default=DEFAULT_OPENAI_MODEL, validation_alias=AliasChoices("OPENAI_MODEL", "TUTORIAL_AI_OPENAI_MODEL")
    )
    openai_tts_model: str = Field(
        default=DEFAULT_OPENAI_TTS_MODEL,
        validation_alias=AliasChoices("OPENAI_TTS_MODEL", "TUTORIAL_AI_OPENAI_TTS_MODEL"),
    )
    gemini_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("GEMINI_API_KEY", "TUTORIAL_AI_GEMINI_API_KEY")
    )
    gemini_model: str = Field(
        default=DEFAULT_GEMINI_MODEL,
        validation_alias=AliasChoices("GEMINI_MODEL", "TUTORIAL_AI_GEMINI_MODEL"),
    )
    # Use the Vertex AI endpoint instead of the Gemini API (AI Studio). Both kinds of key can start
    # with `AQ.`, so this is never guessed from the key.
    gemini_use_vertexai: bool = Field(
        default=False,
        validation_alias=AliasChoices("GOOGLE_GENAI_USE_VERTEXAI", "TUTORIAL_AI_GEMINI_USE_VERTEXAI"),
    )
    ai_temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    ai_max_output_tokens: int = Field(default=32_768, ge=1_024)
    # How many times to re-ask the model when it returns JSON that fails schema validation.
    ai_max_validation_retries: int = Field(default=2, ge=0, le=5)
    ai_request_timeout_seconds: float = Field(default=300.0, gt=0)

    # --- Narration (text-to-speech) ----------------------------------------------------------
    tts_provider: str = Field(  # "openai-tts", "gemini-tts", or "macos-say" (local, free)
        default="openai-tts", validation_alias=AliasChoices("TTS_PROVIDER", "TUTORIAL_AI_TTS_PROVIDER")
    )
    tts_voice: str | None = None  # provider default when unset (Samantha / Kore)
    tts_words_per_minute: int | None = Field(default=None, ge=80, le=300)  # macos-say only
    gemini_tts_model: str = Field(
        default="gemini-3.1-flash-tts-preview",
        validation_alias=AliasChoices("GEMINI_TTS_MODEL", "TUTORIAL_AI_GEMINI_TTS_MODEL"),
    )

    # --- GitHub ------------------------------------------------------------------------------
    # Client ID of your GitHub OAuth App (with Device Flow enabled). Not a secret.
    github_client_id: str | None = Field(
        default=None, validation_alias=AliasChoices("GITHUB_CLIENT_ID", "TUTORIAL_AI_GITHUB_CLIENT_ID")
    )
    # Where the GitHub token is stored (outside the project, owner-only permissions).
    config_directory: Path = Path("~/.config/tutorial-ai")

    # --- Directories -------------------------------------------------------------------------
    output_directory: Path = Path("output")
    # Outside the repository on purpose: tutorials run `uv init`/`uv add` in here, and uv would
    # otherwise register them as workspace members of tutorial-ai's own pyproject.toml.
    workspace_directory: Path = Path("~/.tutorial-ai/workspace")

    # --- Repository analysis -----------------------------------------------------------------
    # Rough budget for repository content sent to the model (estimated at ~4 chars/token).
    max_context_tokens: int = Field(default=120_000, ge=5_000)
    # Files larger than this are listed in the tree but their content is never sent.
    max_file_bytes: int = Field(default=200_000, ge=1_000)
    # Individual files are truncated to this many characters inside the prompt.
    max_chars_per_file: int = Field(default=40_000, ge=1_000)

    # "screen" records the real screen; "test" records a synthetic pattern (for pipeline checks).
    recorder_source: str = "screen"

    log_level: str = "INFO"

    @field_validator("output_directory", "workspace_directory", "config_directory", mode="after")
    @classmethod
    def _expand_home(cls, value: Path) -> Path:
        return value.expanduser()


def ai_model(settings: Settings) -> str:
    """The model name the configured AI provider uses."""
    return settings.openai_model if settings.ai_provider == "openai" else settings.gemini_model


def with_ai_model(settings: Settings, model: str) -> Settings:
    field = "openai_model" if settings.ai_provider == "openai" else "gemini_model"
    return settings.model_copy(update={field: model})


@lru_cache
def get_settings() -> Settings:
    return Settings()
