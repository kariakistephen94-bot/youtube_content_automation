"""TimelineBuilder: scenes + compiled actions + measured audio -> a deterministic schedule.

Per beat: narration segments play back to back from the beat start; the beat's actions start a
moment later (people start talking, then typing). The beat ends when both have finished, plus a
short pause. Actions never overlap narration of a *later* beat, so what is said always matches
what is on screen.
"""

from pydantic import BaseModel

from tutorial_ai.models.action import Action, ActionType
from tutorial_ai.models.scene import Scene, TutorialScenes
from tutorial_ai.models.timeline import SceneTimeline, Timeline, TimelineEvent

ACTION_START_DELAY = 0.4  # seconds after narration starts before the first action of a beat
SEGMENT_GAP = 0.25  # pause between two narration segments
BEAT_PAUSE = 0.5  # breathing room after a beat
LONG_BEAT_PAUSE = 1.2  # after running a command / opening the browser: let the viewer look
SCENE_TAIL = 0.8  # silence at the end of a scene


class BeatSchedule(BaseModel):
    beat_id: str
    start: float
    narration_end: float
    actions_end: float
    end: float


def build_scene_timeline(scene: Scene) -> tuple[SceneTimeline, list[BeatSchedule]]:
    """Requires scene.actions to be compiled and segments to carry measured durations."""
    events: list[TimelineEvent] = []
    schedules: list[BeatSchedule] = []
    actions_by_beat: dict[str, list[Action]] = {}
    for action in scene.actions:
        actions_by_beat.setdefault(action.beat_id or "", []).append(action)
    segments_by_beat: dict[str, list] = {}
    for segment in scene.narration_segments:
        segments_by_beat.setdefault(segment.beat_id or "", []).append(segment)

    t = 0.0
    for beat in scene.beats:
        start = t
        cursor = start
        for segment in segments_by_beat.get(beat.id, []):
            duration = segment.measured_duration_seconds or segment.estimated_duration_seconds
            segment.start_offset_seconds = round(cursor, 3)
            events.append(TimelineEvent(
                time_seconds=round(cursor, 3), duration_seconds=round(duration, 3),
                kind="narration", ref_id=segment.id, description=segment.text[:80],
            ))
            cursor += duration + SEGMENT_GAP
        narration_end = max(start, cursor - SEGMENT_GAP)

        beat_actions = actions_by_beat.get(beat.id, [])
        cursor = start + (ACTION_START_DELAY if segments_by_beat.get(beat.id) else 0.0)
        for action in beat_actions:
            duration = action.expected_duration_seconds or 0.0
            events.append(TimelineEvent(
                time_seconds=round(cursor, 3), duration_seconds=round(duration, 3),
                kind="action", ref_id=action.id, description=_describe(action),
            ))
            cursor += duration + action.wait_after_seconds
        actions_end = cursor if beat_actions else start

        pause = LONG_BEAT_PAUSE if any(
            a.type in (ActionType.RUN_COMMAND, ActionType.OPEN_BROWSER, ActionType.TRY_ENDPOINT)
            for a in beat_actions
        ) else BEAT_PAUSE
        end = max(narration_end, actions_end) + pause
        schedules.append(BeatSchedule(beat_id=beat.id, start=round(start, 3),
                                      narration_end=round(narration_end, 3),
                                      actions_end=round(actions_end, 3), end=round(end, 3)))
        t = end

    events.sort(key=lambda e: e.time_seconds)
    duration = round(t + SCENE_TAIL, 3) if scene.beats else 1.0
    return SceneTimeline(scene_id=scene.id, events=events, duration_seconds=duration), schedules


def build_timeline(project_name: str, scenes: TutorialScenes) -> Timeline:
    scene_timelines = []
    for scene in scenes.scenes:
        timeline, _ = build_scene_timeline(scene)
        scene.estimated_duration_seconds = timeline.duration_seconds
        scene_timelines.append(timeline)
    return Timeline(project_name=project_name, scenes=scene_timelines)


def _describe(action: Action) -> str:
    p = action.parameters.model_dump()
    match action.type:
        case ActionType.TYPE_CODE:
            lines = p["text"].count("\n")
            return f"type {lines} line{'s' if lines != 1 else ''}"
        case ActionType.RUN_COMMAND:
            return f"run {p['command']}"
        case ActionType.OPEN_FILE | ActionType.CREATE_FILE | ActionType.DELETE_FILE:
            return f"{action.type.value.split('_')[0].lower()} {p['path']}"
        case ActionType.HIGHLIGHT_TEXT:
            return f"highlight {p['text'][:30]}"
        case ActionType.OPEN_BROWSER:
            return f"open {p['url']}"
        case ActionType.TRY_ENDPOINT:
            return f"try {p['method'].upper()} {p['path']}"
        case ActionType.KEYBOARD_SHORTCUT:
            return "+".join(p["keys"])
        case _:
            return action.type.value.replace("_", " ").lower()
