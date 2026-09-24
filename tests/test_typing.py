import pytest

from tutorial_ai.automation.typing import HumanTypingEngine, TypingProfile, TypingSpeed


def test_plan_types_every_character_in_order():
    text = 'def f():\n    return "ok"\n'
    strokes = HumanTypingEngine().plan(text)
    assert "".join(k.char for k in strokes) == text


def test_tabs_become_spaces_and_crlf_newlines():
    strokes = HumanTypingEngine().plan("a\r\n\tb")
    assert "".join(k.char for k in strokes) == "a\n    b"


def test_same_seed_same_timing_different_seed_differs():
    text = "from fastapi import FastAPI\n"
    assert HumanTypingEngine(seed=1).plan(text) == HumanTypingEngine(seed=1).plan(text)
    assert HumanTypingEngine(seed=1).plan(text) != HumanTypingEngine(seed=2).plan(text)


def test_duration_matches_speed_on_average():
    text = "x" * 700
    duration = HumanTypingEngine(TypingProfile(code_chars_per_second=7, variation=0.2)).duration(text)
    assert duration == pytest.approx(100, rel=0.05)


def test_pauses_after_newlines_blank_lines_and_punctuation():
    profile = TypingProfile(variation=0, code_chars_per_second=10, newline_pause=0.25,
                            section_pause=1.0, punctuation_pause=0.12)
    engine = HumanTypingEngine(profile)
    strokes = engine.plan("a\nb\n\nc, d")
    delays = {i: k.delay_before for i, k in enumerate(strokes)}
    assert delays[0] == pytest.approx(0.1)
    assert delays[2] == pytest.approx(0.35)  # 'b' after newline
    assert delays[4] == pytest.approx(1.35)  # second '\n' = blank line
    assert delays[7] == pytest.approx(0.22)  # space after comma


def test_indentation_is_typed_faster():
    profile = TypingProfile(variation=0, code_chars_per_second=10, indent_speedup=4, newline_pause=0)
    strokes = HumanTypingEngine(profile).plan("\n    x = 1")
    indent = [k.delay_before for k in strokes[1:5]]
    assert indent == [pytest.approx(0.025)] * 4
    assert strokes[5].delay_before == pytest.approx(0.1)
    # spaces inside a line are normal speed
    assert strokes[7].delay_before == pytest.approx(0.1)


def test_modes_and_presets_change_speed():
    engine = HumanTypingEngine()
    assert engine.duration("uv run pytest", "terminal") < engine.duration("uv run pytest", "code")
    slow = HumanTypingEngine(TypingProfile.preset(TypingSpeed.SLOW)).duration("hello world " * 10)
    fast = HumanTypingEngine(TypingProfile.preset(TypingSpeed.FAST)).duration("hello world " * 10)
    assert slow > 2 * fast
