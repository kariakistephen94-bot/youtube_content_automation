"""SceneBuilder: deterministic conversion of a script into recordable scenes (no AI involved).

Each plan section becomes one or more scenes. Long sections are split at beat boundaries so a
scene stays short enough to re-record cheaply. Narration is split into segments of a few
sentences so audio and on-screen actions can be synchronised in Phase 4.
"""

import re

from tutorial_ai.models.scene import ExpectedState, NarrationSegment, Scene, TutorialScenes
from tutorial_ai.models.script import BeatKind, ScriptBeat, TutorialScript
from tutorial_ai.models.tutorial import TutorialPlan
from tutorial_ai.tutorial import durations
from tutorial_ai.tutorial.workspace_state import WorkspaceState
from tutorial_ai.utils.logging import get_logger

log = get_logger("tutorial.scene_builder")

DEFAULT_MAX_SCENE_SECONDS = 180.0
MAX_SEGMENT_WORDS = 45
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_narration(text: str, max_words: int = MAX_SEGMENT_WORDS) -> list[str]:
    """Group whole sentences into chunks of at most `max_words` (a single long sentence stays whole)."""
    chunks: list[str] = []
    current: list[str] = []
    for sentence in (s.strip() for s in _SENTENCE_END.split(text.strip())):
        if not sentence:
            continue
        if current and durations.word_count(" ".join(current + [sentence])) > max_words:
            chunks.append(" ".join(current))
            current = []
        current.append(sentence)
    if current:
        chunks.append(" ".join(current))
    return chunks


def estimate_beat(beat: ScriptBeat, state: WorkspaceState) -> float:
    typed = ""
    if beat.kind is BeatKind.WRITE_CODE:
        typed = durations.typed_text(state.files.get(beat.file, ""), beat.code_after or "")
    action = durations.action_seconds(beat.kind.value, typed=typed, command=beat.command or "")
    return durations.beat_seconds(durations.narration_seconds(beat.narration), action)


def build_scenes(
    script: TutorialScript,
    plan: TutorialPlan,
    max_scene_seconds: float = DEFAULT_MAX_SCENE_SECONDS,
) -> TutorialScenes:
    state = WorkspaceState()
    scenes: list[Scene] = []

    for section in script.sections:
        # Split into chunks; a new chunk may only start at a narrated beat so every scene opens
        # with the instructor speaking.
        chunks: list[list[tuple[ScriptBeat, float]]] = [[]]
        preview = state.copy()
        for beat in section.beats:
            seconds = estimate_beat(beat, preview)
            preview.apply(beat)
            current_total = sum(s for _, s in chunks[-1])
            if chunks[-1] and beat.narration and current_total + seconds > max_scene_seconds:
                chunks.append([])
            chunks[-1].append((beat, seconds))

        section_plan = plan.section(section.section_number) if section.section_number <= len(
            plan.sections
        ) else None
        for part, chunk in enumerate(chunks, 1):
            beats = [beat for beat, _ in chunk]
            touched: list[str] = []
            for beat in beats:
                state.apply(beat)
                if beat.file and beat.file not in touched:
                    touched.append(beat.file)

            segments = [
                NarrationSegment(
                    id=f"{beat.id}_n{index}",
                    beat_id=beat.id,
                    text=text,
                    estimated_duration_seconds=max(durations.narration_seconds(text), 0.5),
                )
                for beat in beats
                for index, text in enumerate(split_narration(beat.narration), 1)
            ]
            title = section.title if len(chunks) == 1 else f"{section.title} (part {part})"
            scenes.append(
                Scene(
                    id=f"scene_{len(scenes) + 1:03d}",
                    title=title,
                    goal=section_plan.goal if section_plan else section.title,
                    section_number=section.section_number,
                    beats=beats,
                    narration_segments=segments,
                    estimated_duration_seconds=max(round(sum(s for _, s in chunk), 2), 1.0),
                    expected_state=ExpectedState(
                        files_exist=sorted(state.files), running_processes=list(state.running)
                    ),
                    files_involved=touched,
                )
            )

    result = TutorialScenes(title=script.title, scenes=scenes)
    log.info(
        "Built %d scenes from %d sections, estimated %.1f minutes",
        len(scenes), len(script.sections), result.estimated_duration_seconds / 60,
    )
    return result
