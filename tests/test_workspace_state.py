from conftest import MAIN_V1, MAIN_V2, make_sections

from tutorial_ai.models.script import BeatDraft
from tutorial_ai.tutorial.workspace_state import WorkspaceState, check_beats


def _beat(**data) -> BeatDraft:
    return BeatDraft.model_validate({"narration": "x", **data})


def test_replay_tracks_files_and_processes():
    sections = make_sections()
    state = WorkspaceState.replay(sections[1].beats + sections[2].beats)
    assert state.files == {"app/__init__.py": "", "app/main.py": MAIN_V2}
    assert state.running == ["uv run fastapi dev app/main.py"]

    state.apply(sections[3].beats[1])  # stop_process
    assert state.running == []


def test_valid_sequence_has_no_problems():
    sections = make_sections()
    beats = sections[1].beats + sections[2].beats + sections[3].beats
    assert check_beats(beats, WorkspaceState()) == []


def test_highlight_must_match_current_file_content():
    state = WorkspaceState({"app/main.py": MAIN_V1})
    problems = check_beats(
        [_beat(kind="highlight", file="app/main.py", highlight="@app.get")], state
    )
    assert len(problems) == 1 and "does not appear" in problems[0]
    missing = check_beats([_beat(kind="highlight", file="nope.py", highlight="x")], state)
    assert "never created" in missing[0]


def test_other_state_problems():
    empty = WorkspaceState()
    assert "no background process" in check_beats([_beat(kind="stop_process")], empty)[0]
    assert "never created" in check_beats([_beat(kind="delete_file", file="a.py")], empty)[0]

    unchanged = WorkspaceState({"a.py": "x = 1\n"})
    assert "does not change" in check_beats(
        [_beat(kind="write_code", file="a.py", code_after="x = 1\n")], unchanged
    )[0]

    server = _beat(kind="run_command", command="uv run fastapi dev", background=True)
    problems = check_beats([server, server], empty)
    assert len(problems) == 1 and "stop_process" in problems[0]


def test_check_does_not_mutate_the_given_state():
    state = WorkspaceState()
    check_beats([_beat(kind="write_code", file="a.py", code_after="x")], state)
    assert state.files == {}


def test_delete_removes_file():
    state = WorkspaceState({"a.py": "x"})
    state.apply(_beat(kind="delete_file", file="a.py"))
    assert state.files == {}


def test_disallowed_commands_are_problems():
    problems = check_beats([_beat(kind="run_command", command="rm -rf src")], WorkspaceState())
    assert "is not allowed" in problems[0]


def test_large_code_beats_must_be_split():
    big = "".join(f"x{i} = {i}\n" for i in range(30))
    problems = check_beats([_beat(kind="write_code", file="a.py", code_after=big)], WorkspaceState())
    assert "split it into several write_code beats" in problems[0]

    # 30 existing lines + 5 new ones is fine: only the typed difference counts
    state = WorkspaceState({"a.py": big})
    grown = big + "".join(f"y{i} = {i}\n" for i in range(5))
    assert check_beats([_beat(kind="write_code", file="a.py", code_after=grown)], state) == []


def test_package_init_files_must_exist_before_files_inside_the_package():
    state = WorkspaceState(package_dirs={"app", "app/services"})
    problems = check_beats([_beat(kind="write_code", file="app/main.py", code_after="x = 1\n")], state)
    assert problems and "app/__init__.py" in problems[0]

    ok = check_beats([
        _beat(kind="run_command", command="mkdir -p app && touch app/__init__.py"),
        _beat(kind="write_code", file="app/main.py", code_after="x = 1\n"),
        _beat(kind="write_code", file="app/services/__init__.py", code_after=""),
        _beat(kind="write_code", file="app/services/files.py", code_after="y = 2\n"),
    ], state)
    assert ok == []
    # not a package in the reference repo: nothing required
    assert check_beats([_beat(kind="write_code", file="scripts/tool.py", code_after="z\n")], state) == []
