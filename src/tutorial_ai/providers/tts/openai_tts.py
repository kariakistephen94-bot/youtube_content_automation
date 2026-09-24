"""OpenAI text-to-speech (natural voices; billed per character)."""

from pathlib import Path

from openai import APIStatusError, OpenAI

from tutorial_ai.audio.duration import wav_duration
from tutorial_ai.providers.tts.base import AudioResult, TTSError, TTSProvider

DEFAULT_MODEL = "gpt-4o-mini-tts"
DEFAULT_VOICE = "ash"
INSTRUCTIONS = (
    "Speak like a friendly, confident programming YouTuber teaching while coding: natural pace, "
    "clear, warm, a little energetic, never salesy."
)


class OpenAITTSProvider(TTSProvider):
    name = "openai-tts"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, voice: str = DEFAULT_VOICE,
                 client: OpenAI | None = None) -> None:
        self.voice = voice
        self.model = model
        self._client = client or OpenAI(api_key=api_key, timeout=120, max_retries=3)

    def synthesize(self, text: str, output_path: Path) -> AudioResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        kwargs = dict(model=self.model, voice=self.voice, input=text, response_format="wav")
        if "tts-1" not in self.model:  # `instructions` is only supported by the gpt-4o(-mini)-tts models
            kwargs["instructions"] = INSTRUCTIONS
        try:
            response = self._client.audio.speech.create(**kwargs)
        except APIStatusError as exc:
            raise TTSError(f"OpenAI TTS failed ({exc.status_code}): {exc}") from exc
        output_path.write_bytes(response.content)
        duration, rate = wav_duration(output_path)
        return AudioResult(path=output_path, duration_seconds=duration, sample_rate=rate)
