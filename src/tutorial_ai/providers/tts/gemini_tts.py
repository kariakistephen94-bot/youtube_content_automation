"""Gemini text-to-speech (natural voices; uses the Gemini API quota)."""

import wave
from pathlib import Path

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from tutorial_ai.audio.duration import wav_duration
from tutorial_ai.providers.tts.base import AudioResult, TTSError, TTSProvider

DEFAULT_VOICE = "Kore"
SAMPLE_RATE = 24_000  # Gemini TTS returns 16-bit mono PCM at 24 kHz

STYLE_PROMPT = (
    "Read this like a friendly, confident programming YouTuber teaching while coding: natural "
    "pace, clear, warm, not salesy.\n\n"
)


class GeminiTTSProvider(TTSProvider):
    name = "gemini-tts"

    def __init__(self, api_key: str, model: str, voice: str = DEFAULT_VOICE,
                 vertexai: bool = False, client: genai.Client | None = None) -> None:
        self.voice = voice
        self.model = model
        self._client = client or genai.Client(api_key=api_key, vertexai=vertexai)

    def synthesize(self, text: str, output_path: Path) -> AudioResult:
        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice)
                )
            ),
        )
        try:
            response = self._client.models.generate_content(
                model=self.model, contents=STYLE_PROMPT + text, config=config
            )
        except genai_errors.APIError as exc:
            raise TTSError(f"Gemini TTS failed ({getattr(exc, 'code', '?')}): {exc}") from exc

        pcm = _audio_bytes(response)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(SAMPLE_RATE)
            handle.writeframes(pcm)
        duration, rate = wav_duration(output_path)
        return AudioResult(path=output_path, duration_seconds=duration, sample_rate=rate)


def _audio_bytes(response: types.GenerateContentResponse) -> bytes:
    for candidate in response.candidates or []:
        for part in (candidate.content.parts if candidate.content else []) or []:
            if part.inline_data and part.inline_data.data:
                return part.inline_data.data
    raise TTSError("Gemini TTS returned no audio")
