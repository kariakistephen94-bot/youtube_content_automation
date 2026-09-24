"""Local text-to-speech with the macOS `say` command. Free, offline, no quota."""

import subprocess
from pathlib import Path

from tutorial_ai.audio.duration import wav_duration
from tutorial_ai.providers.tts.base import AudioResult, TTSError, TTSProvider

DEFAULT_VOICE = "Samantha"
SAMPLE_RATE = 24_000


class MacOSSayProvider(TTSProvider):
    name = "macos-say"

    def __init__(self, voice: str = DEFAULT_VOICE, words_per_minute: int | None = None) -> None:
        self.voice = voice
        self._rate = words_per_minute

    def synthesize(self, text: str, output_path: Path) -> AudioResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = ["say", "-v", self.voice, "-o", str(output_path),
                   f"--data-format=LEI16@{SAMPLE_RATE}"]
        if self._rate:
            command += ["-r", str(self._rate)]
        # Text goes through stdin so quotes and dashes are never parsed as options.
        result = subprocess.run(command, input=text, capture_output=True, text=True, timeout=300)
        if result.returncode != 0 or not output_path.exists():
            raise TTSError(f"`say` failed for voice {self.voice!r}: {result.stderr.strip()}")
        duration, rate = wav_duration(output_path)
        return AudioResult(path=output_path, duration_seconds=duration, sample_rate=rate)


def available_voices() -> list[str]:
    """English voices installed on this Mac."""
    result = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, check=False)
    voices = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith("en_") or (len(parts) >= 3 and parts[2].startswith("en_")):
            voices.append(line.split("  ")[0].strip())
    return voices
