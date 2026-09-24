"""VideoProcessor: FFmpeg post-processing.

- mux: lay the narration clips onto a silent screen recording at their recorded offsets
- concatenate: join scene videos into tutorial.mp4
- normalize: loudness-normalize the narration track
"""

import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from tutorial_ai.audio.duration import media_duration
from tutorial_ai.recording.recorder import find_ffmpeg
from tutorial_ai.utils.logging import get_logger

log = get_logger("video.processor")


class AudioClip(BaseModel):
    path: Path
    start_seconds: float = Field(ge=0)


def mux_command(ffmpeg: str, video: Path, clips: list[AudioClip], output: Path,
                normalize: bool = True) -> list[str]:
    """Overlay `clips` on `video` (which has no audio track) at their start offsets."""
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video)]
    for clip in clips:
        command += ["-i", str(clip.path)]
    if clips:
        delayed = []
        filters = []
        for index, clip in enumerate(clips, 1):
            ms = int(round(clip.start_seconds * 1000))
            filters.append(f"[{index}:a]adelay={ms}|{ms},aformat=channel_layouts=mono[a{index}]")
            delayed.append(f"[a{index}]")
        mix = f"{''.join(delayed)}amix=inputs={len(clips)}:normalize=0:dropout_transition=0,apad"
        if normalize:
            mix += ",loudnorm=I=-16:TP=-1.5:LRA=11"
        filters.append(f"{mix}[aout]")
        command += ["-filter_complex", ";".join(filters), "-map", "0:v", "-map", "[aout]",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest"]
    else:
        command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-shortest"]
    command += ["-movflags", "+faststart", str(output)]
    return command


class VideoProcessor:
    def __init__(self) -> None:
        self._ffmpeg = find_ffmpeg()

    def mux(self, video: Path, clips: list[AudioClip], output: Path, normalize: bool = True) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        _run(mux_command(self._ffmpeg, video, clips, output, normalize))
        log.info("Muxed %d narration clips into %s (%.1fs)", len(clips), output.name, media_duration(output))
        return output

    def concatenate(self, videos: list[Path], output: Path) -> Path:
        if not videos:
            raise ValueError("nothing to concatenate")
        output.parent.mkdir(parents=True, exist_ok=True)
        list_file = output.with_suffix(".concat.txt")
        list_file.write_text("".join(f"file '{v.resolve()}'\n" for v in videos), encoding="utf-8")
        # Scenes share codec settings, so a stream copy keeps this fast and lossless.
        _run([self._ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
              "-i", str(list_file), "-c", "copy", "-movflags", "+faststart", str(output)])
        list_file.unlink(missing_ok=True)
        log.info("Wrote %s (%.1f min)", output, media_duration(output) / 60)
        return output


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()[-800:]}")
