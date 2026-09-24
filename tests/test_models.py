import pytest
from pydantic import ValidationError

from tutorial_ai.models import (
    Action,
    ActionType,
    FailureBehavior,
    NarrationSegment,
    Scene,
    SceneTimeline,
    TimelineEvent,
    TutorialPlan,
)
from tutorial_ai.models.action import PARAMS_BY_TYPE, RunCommandParams, WorkspacePathParams
from tutorial_ai.models.repository import RepositoryInsights

from conftest import make_insights


# --- Actions ------------------------------------------------------------------------------------


def test_every_action_type_has_a_parameter_model():
    assert set(PARAMS_BY_TYPE) == set(ActionType)


def test_action_parameters_are_parsed_by_type():
    action = Action.model_validate(
        {"id": "a1", "type": "RUN_COMMAND", "parameters": {"command": "uv run fastapi dev"}}
    )
    assert isinstance(action.parameters, RunCommandParams)
    assert action.parameters.command == "uv run fastapi dev"
    assert action.failure_behavior is FailureBehavior.PAUSE


def test_action_round_trips_through_json():
    action = Action.model_validate(
        {"id": "a1", "type": "OPEN_FILE", "parameters": {"path": "app/main.py"}}
    )
    restored = Action.model_validate_json(action.model_dump_json())
    assert restored == action
    assert '"path":"app/main.py"' in action.model_dump_json()


def test_action_without_parameters():
    assert Action(id="a1", type=ActionType.SAVE_FILE).parameters.model_dump() == {}


@pytest.mark.parametrize(
    ("action_type", "parameters"),
    [
        ("OPEN_FILE", {}),  # missing path
        ("TYPE_CODE", {"text": ""}),  # empty text
        ("RUN_COMMAND", {"command": "ls", "shell": True}),  # unknown field
        ("SAVE_FILE", {"path": "x.py"}),  # takes no parameters
        ("NAVIGATE_BROWSER", {"url": "file:///etc/passwd"}),  # only http(s)
        ("WAIT", {"seconds": 0}),
        ("NOT_A_TYPE", {}),
    ],
)
def test_invalid_actions_rejected(action_type, parameters):
    with pytest.raises((ValidationError, ValueError)):
        Action.model_validate({"id": "a1", "type": action_type, "parameters": parameters})


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside.py", "app/../../x", "~/x", " "])
def test_paths_cannot_escape_the_workspace(path):
    with pytest.raises(ValidationError):
        WorkspacePathParams(path=path)


# --- Scenes -------------------------------------------------------------------------------------


def _scene(**overrides) -> dict:
    data = {
        "id": "scene_001",
        "title": "Create the app",
        "goal": "A running FastAPI app",
        "estimated_duration_seconds": 30,
        "narration_segments": [
            {"id": "n1", "text": "Let's create main.py.", "estimated_duration_seconds": 2,
             "linked_actions": ["a1"]},
        ],
        "actions": [
            {"id": "a1", "type": "CREATE_FILE", "parameters": {"path": "main.py"}},
            {"id": "a2", "type": "TYPE_CODE", "parameters": {"text": "print(1)"}, "start_after": "a1"},
        ],
    }
    data.update(overrides)
    return data


def test_valid_scene():
    scene = Scene.model_validate(_scene())
    assert scene.actions[1].start_after == "a1"


def test_scene_rejects_unknown_linked_action():
    data = _scene()
    data["narration_segments"][0]["linked_actions"] = ["missing"]
    with pytest.raises(ValidationError, match="links unknown actions"):
        Scene.model_validate(data)


def test_scene_rejects_duplicate_ids():
    data = _scene()
    data["actions"][1]["id"] = "a1"
    data["actions"][1]["start_after"] = None
    with pytest.raises(ValidationError, match="duplicate ids"):
        Scene.model_validate(data)


def test_scene_requires_narration():
    with pytest.raises(ValidationError):
        Scene.model_validate(_scene(narration_segments=[]))


def test_narration_segment_duration_positive():
    with pytest.raises(ValidationError):
        NarrationSegment(id="n1", text="hi", estimated_duration_seconds=0)


# --- Timeline / plan ----------------------------------------------------------------------------


def test_timeline_events_must_be_sorted_and_fit():
    events = [
        TimelineEvent(time_seconds=0, duration_seconds=3, kind="narration", ref_id="n1"),
        TimelineEvent(time_seconds=1, duration_seconds=2, kind="action", ref_id="a1"),
    ]
    assert SceneTimeline(scene_id="s1", events=events, duration_seconds=3).duration_seconds == 3
    with pytest.raises(ValidationError, match="sorted"):
        SceneTimeline(scene_id="s1", events=events[::-1], duration_seconds=3)
    with pytest.raises(ValidationError, match="after the scene duration"):
        SceneTimeline(scene_id="s1", events=events, duration_seconds=2)


def test_tutorial_plan_sections_numbered_in_order():
    section = {
        "title": "Setup", "goal": "g", "expected_result": "r", "estimated_duration_seconds": 60
    }
    plan = {
        "title": "T", "audience": "beginner", "difficulty": "beginner", "summary": "s",
        "final_architecture": "a",
        "learning_objectives": ["x"], "estimated_duration_seconds": 120,
        "sections": [{**section, "section_number": 1}, {**section, "section_number": 3}],
    }
    with pytest.raises(ValidationError, match="numbered"):
        TutorialPlan.model_validate(plan)


# --- Repository insights (AI response schema) ---------------------------------------------------


def test_insights_schema_is_self_contained_json_schema():
    schema = RepositoryInsights.model_json_schema()
    assert schema["additionalProperties"] is False
    assert "potential_teaching_order" in schema["required"]
    assert "description" in schema["properties"]["potential_teaching_order"]


def test_insights_reject_unknown_fields_and_bad_enum():
    valid = make_insights().model_dump()
    with pytest.raises(ValidationError):
        RepositoryInsights.model_validate({**valid, "surprise": 1})
    with pytest.raises(ValidationError):
        RepositoryInsights.model_validate({**valid, "difficulty": "expert"})
