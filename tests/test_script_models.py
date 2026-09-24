import pytest
from pydantic import ValidationError

from tutorial_ai.models.script import BeatDraft, ScriptSectionDraft


@pytest.mark.parametrize(
    "narration",
    [
        "Let's delve into FastAPI.",
        "This will unlock the power of async.",
        "A game-changing feature.",
        "FastAPI can Revolutionize your APIs.",
        "Without further ado, let's code.",
    ],
)
def test_ai_sounding_narration_is_rejected(narration):
    with pytest.raises(ValidationError, match="sounds machine-written"):
        BeatDraft(kind="talk", narration=narration)


def test_natural_narration_is_accepted():
    beat = BeatDraft(kind="talk", narration="  Alright, let's start by creating the project.  ")
    assert beat.narration == "Alright, let's start by creating the project."


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"kind": "write_code", "narration": "x", "file": "main.py"}, "requires code_after"),
        ({"kind": "write_code", "narration": "x", "code_after": "print(1)"}, "requires file"),
        ({"kind": "run_command", "narration": "x"}, "requires command"),
        ({"kind": "highlight", "narration": "x", "file": "main.py"}, "requires highlight"),
        ({"kind": "open_browser", "narration": "x"}, "requires url"),
        ({"kind": "open_browser", "narration": "x", "url": "localhost:8000"}, "http"),
        ({"kind": "talk", "narration": ""}, "needs narration"),
        ({"kind": "delete_file", "narration": "x", "file": "../etc/passwd"}, "escape"),
        ({"kind": "talk", "narration": "x", "extra": 1}, "Extra inputs"),
    ],
)
def test_beat_fields_must_match_kind(data, message):
    with pytest.raises(ValidationError, match=message):
        BeatDraft.model_validate(data)


def test_file_paths_are_normalised():
    beat = BeatDraft(kind="write_code", narration="", file="./app/main.py", code_after="x = 1\n")
    assert beat.file == "app/main.py"


def test_section_must_open_with_narration():
    with pytest.raises(ValidationError, match="first beat"):
        ScriptSectionDraft.model_validate({
            "beats": [{"kind": "write_code", "narration": "", "file": "a.py", "code_after": "x"}],
            "state_summary": "s",
        })


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"kind": "try_endpoint", "narration": "x", "url": "http://127.0.0.1:8000/docs"}, "requires method, path"),
        ({"kind": "try_endpoint", "narration": "x", "url": "http://127.0.0.1:8000/docs", "method": "fetch", "path": "/"}, "method must be one of"),
        ({"kind": "try_endpoint", "narration": "x", "url": "http://127.0.0.1:8000/docs", "method": "get", "path": "upload"}, "route path"),
        ({"kind": "try_endpoint", "narration": "x", "url": "http://127.0.0.1:8000/docs", "method": "post", "path": "/x", "body": "{not json"}, "valid JSON"),
        ({"kind": "try_endpoint", "narration": "x", "url": "http://127.0.0.1:8000/docs", "method": "post", "path": "/upload",
          "params": [{"name": "file", "value": "@hello.txt"}]}, "not declared in sample_files"),
        ({"kind": "try_endpoint", "narration": "x", "url": "http://127.0.0.1:8000/docs", "method": "post", "path": "/upload",
          "params": [{"name": "file", "value": "@a.txt"}], "sample_files": [{"name": "../a.txt", "content": ""}]}, "plain file name"),
    ],
)
def test_try_endpoint_fields_are_validated(data, message):
    with pytest.raises(ValidationError, match=message):
        BeatDraft.model_validate(data)


def test_try_endpoint_beat_normalises_and_exposes_request_values():
    beat = BeatDraft.model_validate({
        "kind": "try_endpoint", "narration": "Let's try it.", "url": "http://127.0.0.1:8000/docs",
        "method": "POST", "path": " /upload ", "params": [{"name": "file", "value": "@hello.txt"}, {"name": "note", "value": "hi"}],
        "sample_files": [{"name": "hello.txt", "content": "Hello!\n"}],
    })
    assert beat.method == "post" and beat.path == "/upload"
    assert beat.param_values == {"file": "@hello.txt", "note": "hi"}
    assert beat.sample_file_contents == {"hello.txt": "Hello!\n"}
