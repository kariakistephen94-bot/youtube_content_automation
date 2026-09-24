import pytest
from conftest import make_plan, make_sections

from tutorial_ai.automation.actions import ActionCompiler, normalize, plan_edits
from tutorial_ai.models.action import ActionType
from tutorial_ai.models.script import ScriptBeat, ScriptSection, TutorialScript
from tutorial_ai.models.timeline import Timeline
from tutorial_ai.timeline.builder import build_scene_timeline, build_timeline
from tutorial_ai.tutorial.scene_builder import build_scenes
from tutorial_ai.tutorial.workspace_state import WorkspaceState


def _apply(before: str, edits) -> str:
    """Apply line edits the way VS Code would, to prove they reproduce the target file."""
    lines = normalize(before).splitlines(keepends=True)
    for edit in edits:
        index = edit.line - 1
        if edit.kind == "delete":
            del lines[index:index + edit.count]
        else:
            lines[index:index] = edit.text.splitlines(keepends=True)
    return "".join(lines)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("", "print(1)\n"),
        ("a\n", "a\nb\nc\n"),  # append
        ("a\nb\nc\n", "a\nc\n"),  # delete middle
        ("a\nb\nc\nd\n", "a\nX\nY\nd\nE\n"),  # replace + append
        ("import os\n\ndef f():\n    pass\n", "import os\nimport sys\n\ndef f():\n    return sys.argv\n"),
        ("a\nb\n", "b\na\n"),
        ("x\n", "x"),  # trailing newline is normalized away
    ],
)
def test_edits_reproduce_the_target_file(before, after):
    assert _apply(before, plan_edits(before, after)) == normalize(after)


def test_unchanged_file_needs_no_edits():
    assert plan_edits("a\nb\n", "a\nb\n") == []


def _scenes():
    sections = []
    for number, draft in make_sections().items():
        beats = [ScriptBeat(id=f"s{number:02d}_b{i:02d}", **b.model_dump()) for i, b in enumerate(draft.beats, 1)]
        sections.append(ScriptSection(section_number=number, title=make_plan().section(number).title,
                                      beats=beats, state_summary=draft.state_summary))
    return build_scenes(TutorialScript(title="T", sections=sections), make_plan())


def test_compiler_types_only_the_difference():
    scenes = _scenes()
    state = WorkspaceState()
    compiler = ActionCompiler()
    compiled = [compiler.compile_scene(scene, state) for scene in scenes.scenes]

    setup = compiled[1].actions  # scene 2: create main.py, add health route, run server, browser
    types = [a.type for a in setup]
    assert types[:2] == [ActionType.OPEN_TERMINAL, ActionType.RUN_COMMAND]  # uv add
    assert ActionType.CREATE_FILE in types and ActionType.SAVE_FILE in types
    assert types.count(ActionType.TYPE_CODE) == 2  # first version, then the appended route
    appended = [a for a in setup if a.type is ActionType.TYPE_CODE][1]
    assert appended.parameters.text.startswith("\n\n@app.get")  # not the whole file again
    assert ActionType.HIGHLIGHT_TEXT in types and ActionType.OPEN_BROWSER in types
    run = [a for a in setup if a.type is ActionType.RUN_COMMAND][1]
    assert run.parameters.background and run.parameters.wait_url == "http://127.0.0.1:8000/docs"

    upload = compiled[2].actions  # scene 3: append endpoint, stop server
    assert [a.type for a in upload][:3] == [ActionType.OPEN_FILE, ActionType.KEYBOARD_SHORTCUT, ActionType.TYPE_CODE]
    assert upload[1].parameters.keys == ["cmd", "end"]
    assert upload[-1].parameters.keys == ["ctrl", "c"]
    assert all(a.beat_id and a.expected_duration_seconds is not None for a in upload)
    assert [a.start_after for a in upload[1:]] == [a.id for a in upload[:-1]]


def test_compiled_actions_validate_inside_the_scene_model():
    scenes = _scenes()
    state = WorkspaceState()
    for scene in scenes.scenes:
        scene.actions = ActionCompiler().compile_scene(scene, state).actions
        for segment in scene.narration_segments:
            segment.linked_actions = [a.id for a in scene.actions if a.beat_id == segment.beat_id]
        type(scene).model_validate(scene.model_dump())  # id consistency validator runs


def test_timeline_orders_narration_then_actions_and_never_overlaps_beats():
    scenes = _scenes()
    state = WorkspaceState()
    for scene in scenes.scenes:
        scene.actions = ActionCompiler().compile_scene(scene, state).actions
        for segment in scene.narration_segments:
            segment.measured_duration_seconds = 3.0
    timeline, schedules = build_scene_timeline(scenes.scenes[1])

    assert timeline.events[0].kind == "narration" and timeline.events[0].time_seconds == 0
    first_action = next(e for e in timeline.events if e.kind == "action")
    assert first_action.time_seconds == pytest.approx(0.4)
    for a, b in zip(schedules, schedules[1:]):
        assert b.start >= a.end  # a beat starts only after the previous one fully finished
        assert a.end >= max(a.narration_end, a.actions_end)
    assert timeline.duration_seconds > schedules[-1].end
    assert scenes.scenes[1].narration_segments[1].start_offset_seconds is not None


def test_full_timeline_round_trips_and_updates_scene_durations():
    scenes = _scenes()
    for scene in scenes.scenes:
        for segment in scene.narration_segments:
            segment.measured_duration_seconds = 2.0
    timeline = build_timeline("demo", scenes)
    assert Timeline.model_validate_json(timeline.model_dump_json()) == timeline
    assert timeline.total_duration_seconds == pytest.approx(sum(s.estimated_duration_seconds for s in scenes.scenes))


def test_empty_files_compile_to_create_without_typing():
    from tutorial_ai.models.scene import NarrationSegment, Scene
    from tutorial_ai.models.script import ScriptBeat

    beat = ScriptBeat(id="b1", kind="write_code", narration="Create the package.", file="app/__init__.py", code_after="")
    scene = Scene(id="scene_001", title="t", goal="g", beats=[beat], estimated_duration_seconds=5,
                  narration_segments=[NarrationSegment(id="b1_n1", beat_id="b1", text="Create the package.", estimated_duration_seconds=1)])
    compiled = ActionCompiler().compile_scene(scene, WorkspaceState())
    assert [a.type for a in compiled.actions] == [ActionType.CREATE_FILE, ActionType.SAVE_FILE]


def test_highlight_gets_a_keyboard_position_from_the_known_file():
    from tutorial_ai.automation.actions import find_position

    content = "from fastapi import FastAPI\n\napp = FastAPI()\n\n@app.get('/')\ndef health():\n    return 1\n"
    assert find_position(content, "@app.get('/')") == (5, 1)
    assert find_position(content, "FastAPI()") == (3, 7)
    assert find_position(content, "missing") is None
    scenes = _scenes()
    state = WorkspaceState()
    compiled = [ActionCompiler().compile_scene(scene, state) for scene in scenes.scenes]
    highlight = next(a for a in compiled[1].actions if a.type is ActionType.HIGHLIGHT_TEXT)
    assert highlight.parameters.line == 6 and highlight.parameters.column == 1


def test_try_endpoint_compiles_to_one_browser_action_and_proves_the_server_url():
    from tutorial_ai.models.scene import NarrationSegment, Scene

    beats = [
        ScriptBeat(id="b1", kind="run_command", narration="Run it.", command="uv run fastapi dev app/main.py", background=True),
        ScriptBeat(id="b2", kind="try_endpoint", narration="Upload a file.", url="http://127.0.0.1:8000/docs",
                   method="post", path="/upload", params=[{"name": "file", "value": "@hello.txt"}],
                   sample_files=[{"name": "hello.txt", "content": "Hello!\n"}]),
    ]
    scene = Scene(id="scene_001", title="t", goal="g", beats=beats, estimated_duration_seconds=5,
                  narration_segments=[NarrationSegment(id="b1_n1", beat_id="b1", text="Run it.", estimated_duration_seconds=1)])
    compiled = ActionCompiler().compile_scene(scene, WorkspaceState())
    types = [a.type for a in compiled.actions]
    assert types == [ActionType.OPEN_TERMINAL, ActionType.RUN_COMMAND, ActionType.TRY_ENDPOINT]
    run, try_ = compiled.actions[1], compiled.actions[2]
    assert run.parameters.wait_url == "http://127.0.0.1:8000/docs"  # the docs page proves the server is up
    assert try_.parameters.method == "post" and try_.parameters.path == "/upload"
    assert try_.parameters.params == {"file": "@hello.txt"} and try_.parameters.sample_files == {"hello.txt": "Hello!\n"}
    assert try_.expected_duration_seconds > 14
    timeline, schedules = build_scene_timeline(scene.model_copy(update={"actions": compiled.actions}))
    assert any("try POST /upload" in e.description for e in timeline.events)


def test_try_endpoint_needs_a_running_server():
    from tutorial_ai.models.script import BeatDraft
    from tutorial_ai.tutorial.workspace_state import check_beats

    beat = BeatDraft(kind="try_endpoint", narration="x", url="http://127.0.0.1:8000/docs", method="get", path="/")
    assert check_beats([beat], WorkspaceState()) == ["beat 1 (try_endpoint): try_endpoint get /: no server is running; start it with a background run_command (and show it with open_browser) first"]
    assert check_beats([beat], WorkspaceState(running=["uv run fastapi dev app/main.py"])) == []
