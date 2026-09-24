"""NarrationGenerator: one audio file per narration segment, with measured durations.

Resumable: segments whose audio already exists (same text, same provider/voice) are kept.
Results are written back into scenes.json (audio_path + measured duration) and summarised in
narration.json.
"""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from tutorial_ai.audio.duration import wav_duration
from tutorial_ai.models.scene import TutorialScenes
from tutorial_ai.providers.tts.base import TTSProvider
from tutorial_ai.utils.logging import get_logger

log = get_logger("audio.generator")


class NarrationEntry(BaseModel):
    scene_id: str
    segment_id: str
    text: str
    audio_path: str  # relative to the project directory
    duration_seconds: float = Field(gt=0)
    text_hash: str


class NarrationManifest(BaseModel):
    provider: str
    voice: str
    generated_at: datetime
    entries: list[NarrationEntry] = Field(default_factory=list)

    @property
    def total_seconds(self) -> float:
        return sum(e.duration_seconds for e in self.entries)


def text_hash(text: str, provider: str, voice: str) -> str:
    return hashlib.sha1(f"{provider}|{voice}|{text}".encode()).hexdigest()[:12]


class NarrationGenerator:
    def __init__(self, provider: TTSProvider, project_dir: Path) -> None:
        self._provider = provider
        self._project_dir = project_dir
        self._audio_dir = project_dir / "audio"

    def generate(
        self,
        scenes: TutorialScenes,
        existing: NarrationManifest | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> NarrationManifest:
        reusable = {}
        if existing and (existing.provider, existing.voice) == (self._provider.name, self._provider.voice):
            reusable = {e.segment_id: e for e in existing.entries
                        if (self._project_dir / e.audio_path).exists()}

        manifest = NarrationManifest(
            provider=self._provider.name, voice=self._provider.voice, generated_at=datetime.now(UTC)
        )
        total = sum(len(s.narration_segments) for s in scenes.scenes)
        done = 0
        for scene in scenes.scenes:
            for segment in scene.narration_segments:
                done += 1
                digest = text_hash(segment.text, self._provider.name, self._provider.voice)
                entry = reusable.get(segment.id)
                if entry is None or entry.text_hash != digest:
                    path = self._audio_dir / scene.id / f"{segment.id}.wav"
                    result = self._provider.synthesize(segment.text, path)
                    entry = NarrationEntry(
                        scene_id=scene.id, segment_id=segment.id, text=segment.text,
                        audio_path=str(path.relative_to(self._project_dir)),
                        duration_seconds=round(result.duration_seconds, 3), text_hash=digest,
                    )
                    log.info("[%d/%d] %s: %.1fs", done, total, segment.id, entry.duration_seconds)
                else:
                    # Re-measure reused clips so a duration fix never requires regenerating audio.
                    measured, _ = wav_duration(self._project_dir / entry.audio_path)
                    entry = entry.model_copy(update={"duration_seconds": round(measured, 3)})
                    log.debug("[%d/%d] %s: reused", done, total, segment.id)
                manifest.entries.append(entry)
                segment.audio_path = entry.audio_path
                segment.measured_duration_seconds = entry.duration_seconds
        log.info("Narration: %d segments, %.1f minutes of audio", len(manifest.entries),
                 manifest.total_seconds / 60)
        return manifest
