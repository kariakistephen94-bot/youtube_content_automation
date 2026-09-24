"""Provider-agnostic text-to-speech interface.

Every provider writes a mono 16-bit PCM WAV file and reports its measured duration, so the
timeline is built from real audio lengths, never estimates.
"""

from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, Field


class TTSError(RuntimeError):
    pass


class AudioResult(BaseModel):
    path: Path
    duration_seconds: float = Field(gt=0)
    sample_rate: int


class TTSProvider(ABC):
    name: str
    voice: str

    @abstractmethod
    def synthesize(self, text: str, output_path: Path) -> AudioResult:
        """Write `text` as speech to `output_path` (WAV) and return its measured duration."""
