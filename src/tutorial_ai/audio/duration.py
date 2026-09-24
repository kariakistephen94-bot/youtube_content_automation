"""Measure real audio durations (WAV via the standard library; anything else via ffprobe)."""

import json
import shutil
import subprocess
import wave
from pathlib import Path


def wav_duration(path: Path) -> tuple[float, int]:
    """(duration in seconds, sample rate) of a PCM WAV file.

    Streamed WAVs (e.g. from OpenAI TTS) carry a placeholder length in the header, so the frame
    count is derived from the real data size whenever the header disagrees with the file.
    """
    with wave.open(str(path), "rb") as handle:
        frames = handle.getnframes()
        rate = handle.getframerate()
        frame_bytes = handle.getnchannels() * handle.getsampwidth()
    data_bytes = max(path.stat().st_size - 44, 0)  # 44 = canonical RIFF/fmt/data header size
    frames_by_size = data_bytes // frame_bytes if frame_bytes else 0
    if frames <= 0 or frames * frame_bytes > data_bytes + frame_bytes:
        frames = frames_by_size
    if rate <= 0 or frames <= 0:
        raise ValueError(f"{path} contains no audio")
    return frames / rate, rate


def media_duration(path: Path) -> float:
    """Duration of any media file via ffprobe (used for recordings)."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise RuntimeError("ffprobe (part of FFmpeg) is not installed or not on PATH")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])
