import pytest
from conftest import MAIN_V1, make_plan, make_sections

from tutorial_ai.models.scene import TutorialScenes
from tutorial_ai.models.script import ScriptBeat, ScriptSection, TutorialScript
from tutorial_ai.tutorial import durations
from tutorial_ai.tutorial.scene_builder import build_scenes, split_narration


def make_script() -> TutorialScript:
    sections = []
    for number, draft in make_sections().items():
        beats = [
            ScriptBeat(id=f"s{number:02d}_b{i:02d}", **b.model_dump())
            for i, b in enumerate(draft.beats, 1)
        ]
        sections.append(ScriptSection(
            section_number=number, title=make_plan().section(number).title,
            beats=beats, state_summary=draft.state_summary,
        ))
    return TutorialScript(title="T", sections=sections)


# --- durations ----------------------------------------------------------------------------------


def test_narration_seconds():
    assert durations.narration_seconds("") == 0
    assert durations.narration_seconds("one two three four five") == 2.0  # 2.5 words/sec


def test_typed_text_is_only_the_difference():
    before = "a = 1\n"
    after = "a = 1\nb = 2\n"
    assert durations.typed_text(before, after) == "b = 2\n"
    assert durations.typed_text("", after) == after
    assert durations.typed_text(after, after) == ""


def test_typing_seconds_uses_speed_and_newline_pauses():
    text = "x" * 70 + "\n" * 4
    assert durations.typing_seconds(text, 7.0) == pytest.approx(74 / 7 + 4 * 0.25)


def test_beat_seconds_overlaps_narration_and_action():
    assert durations.beat_seconds(narration=5, action=2) == 5.3
    assert durations.beat_seconds(narration=1, action=4) == 4.3


# --- narration splitting ------------------------------------------------------------------------


def test_split_narration_keeps_whole_sentences():
    text = "First sentence here. " * 10  # 30 words
    chunks = split_narration(text, max_words=10)
    assert all(c.endswith(".") for c in chunks)
    assert all(durations.word_count(c) <= 10 for c in chunks)
    assert " ".join(chunks) == text.strip()


def test_split_narration_short_text_is_one_segment():
    assert split_narration("Let's run this. Looks good!") == ["Let's run this. Looks good!"]
    assert split_narration("") == []


# --- scenes -------------------------------------------------------------------------------------


def test_one_scene_per_short_section():
    result = build_scenes(make_script(), make_plan())
    assert [s.id for s in result.scenes] == ["scene_001", "scene_002", "scene_003"]
    assert [s.section_number for s in result.scenes] == [1, 2, 3]
    assert result.scenes[1].goal == "goal 2"


def test_scene_contents_and_expected_state():
    scene = build_scenes(make_script(), make_plan()).scenes[1]
    assert scene.files_involved == ["app/main.py"]
    assert scene.expected_state.files_exist == ["app/__init__.py", "app/main.py"]
    assert scene.expected_state.running_processes == ["uv run fastapi dev app/main.py"]
    # the silent open_browser beat produces no narration segment
    assert len(scene.beats) == 6 and len(scene.narration_segments) == 5
    assert all(seg.beat_id in {b.id for b in scene.beats} for seg in scene.narration_segments)
    assert scene.narration_segments[0].id == "s02_b01_n1"
    assert scene.estimated_duration_seconds > 0


def test_long_sections_are_split_at_narrated_beats():
    result = build_scenes(make_script(), make_plan(), max_scene_seconds=20)
    assert len(result.scenes) > 3
    titles = [s.title for s in result.scenes if s.section_number == 2]
    assert titles[0] == "First FastAPI app (part 1)"
    for scene in result.scenes:
        assert scene.beats[0].narration, "every scene must open with narration"
    # every beat appears exactly once, in order
    all_beats = [b.id for s in result.scenes for b in s.beats]
    assert all_beats == [b.id for b in make_script().beats]


def test_scenes_round_trip_json():
    result = build_scenes(make_script(), make_plan())
    assert TutorialScenes.model_validate_json(result.model_dump_json()) == result


def test_typing_estimate_counts_only_new_code():
    script = make_script()
    scenes = build_scenes(script, make_plan()).scenes
    # section 3 only appends the upload endpoint; it must not be charged for retyping the file
    section3 = scenes[2]
    full_retype = durations.typing_seconds(script.sections[2].beats[0].code_after)
    assert section3.estimated_duration_seconds < full_retype
    assert MAIN_V1 in script.sections[2].beats[0].code_after
