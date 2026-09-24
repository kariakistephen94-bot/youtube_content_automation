"""ScenePlayer: perform one scene in VS Code in step with its narration schedule.

Beat by beat: note the wall-clock start, (optionally) play the narration aloud, run the beat's
actions, then wait until the narration would have finished before moving on. The actual start
time of every beat and narration segment is recorded, so the clean narration audio can later be
muxed exactly where the viewer would expect it - even if typing or a command ran long.
"""

import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from tutorial_ai.automation.executor import ActionExecutor
from tutorial_ai.models.action import Action
from tutorial_ai.models.recording import RecordedBeat, RecordedSegment
from tutorial_ai.models.scene import Scene
from tutorial_ai.timeline.builder import BEAT_PAUSE, LONG_BEAT_PAUSE, SEGMENT_GAP, build_scene_timeline
from tutorial_ai.utils.logging import get_logger

log = get_logger("automation.orchestrator")


class ScenePlayer:
    def __init__(self, executor: ActionExecutor, project_dir: Path, audible: bool = False,
                 on_progress: Callable[[str], None] | None = None) -> None:
        self._executor = executor
        self._project_dir = project_dir
        self._audible = audible
        self._progress = on_progress or (lambda message: None)
        self._players: list[subprocess.Popen] = []

    def play(self, scene: Scene, clock_start: float | None = None) -> tuple[list[RecordedBeat], list[RecordedSegment]]:
        """Perform the scene. Times are seconds since `clock_start` (default: now)."""
        _, schedule = build_scene_timeline(scene)
        pauses = {s.beat_id: s.end - max(s.narration_end, s.actions_end) for s in schedule}
        actions_by_beat: dict[str, list[Action]] = {}
        for action in scene.actions:
            actions_by_beat.setdefault(action.beat_id or "", []).append(action)
        segments_by_beat: dict[str, list] = {}
        for segment in scene.narration_segments:
            segments_by_beat.setdefault(segment.beat_id or "", []).append(segment)

        origin = clock_start if clock_start is not None else time.monotonic()
        beats: list[RecordedBeat] = []
        segments: list[RecordedSegment] = []
        try:
            for beat in scene.beats:
                start = time.monotonic()
                self._progress(f"{beat.id} ({beat.kind.value}): {beat.narration[:70]}")
                narration_seconds = 0.0
                for segment in segments_by_beat.get(beat.id, []):
                    segments.append(RecordedSegment(
                        segment_id=segment.id, start_seconds=round(start - origin + narration_seconds, 3)
                    ))
                    duration = segment.measured_duration_seconds or segment.estimated_duration_seconds
                    self._play_audio(segment.audio_path, delay=narration_seconds)
                    narration_seconds += duration + SEGMENT_GAP
                narration_seconds = max(narration_seconds - SEGMENT_GAP, 0.0)

                if segments_by_beat.get(beat.id) and actions_by_beat.get(beat.id):
                    time.sleep(0.4)  # start talking, then start doing
                for action in actions_by_beat.get(beat.id, []):
                    self._executor.execute(action)

                remaining = narration_seconds - (time.monotonic() - start)
                if remaining > 0:
                    time.sleep(remaining)
                time.sleep(pauses.get(beat.id, BEAT_PAUSE))
                beats.append(RecordedBeat(beat_id=beat.id, start_seconds=round(start - origin, 3),
                                          end_seconds=round(time.monotonic() - origin, 3)))
        finally:
            self._stop_audio()
        return beats, segments

    def _play_audio(self, relative: str | None, delay: float) -> None:
        if not self._audible or not relative:
            return
        path = self._project_dir / relative
        command = f"sleep {delay:.3f}; afplay '{path}'" if delay else f"afplay '{path}'"
        self._players.append(subprocess.Popen(command, shell=True))

    def _stop_audio(self) -> None:
        for player in self._players:
            if player.poll() is None:
                player.terminate()
        self._players.clear()


__all__ = ["ScenePlayer", "LONG_BEAT_PAUSE"]
