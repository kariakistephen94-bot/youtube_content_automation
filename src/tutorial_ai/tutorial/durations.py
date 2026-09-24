"""Duration estimates used before real audio exists.

Phase 3 replaces narration estimates with measured TTS durations, and Phase 4's typing engine
replaces the typing estimates. These are only for planning and scene splitting.
"""

import difflib
import re

NARRATION_WORDS_PER_SECOND = 2.5  # ~150 wpm, a relaxed YouTube-tutorial pace
CODE_CHARS_PER_SECOND = 7.0
TERMINAL_CHARS_PER_SECOND = 8.0
NEWLINE_PAUSE_SECONDS = 0.25

_ACTION_OVERHEAD = {
    "talk": 0.0,
    "write_code": 1.0,  # open/focus the file
    "delete_file": 1.5,
    "run_command": 2.0,  # focus terminal + wait for a short command
    "stop_process": 1.0,
    "highlight": 1.5,
    "open_browser": 3.0,
    "try_endpoint": 14.0,  # expand, Try it out, fill, Execute, read the response
}


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def narration_seconds(text: str) -> float:
    words = word_count(text)
    return round(words / NARRATION_WORDS_PER_SECOND, 2) if words else 0.0


def typed_text(before: str, after: str) -> str:
    """The text that has to be typed to turn `before` into `after` (inserted/replaced parts)."""
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    return "".join(
        after[j1:j2] for tag, _, _, j1, j2 in matcher.get_opcodes() if tag in ("insert", "replace")
    )


def typing_seconds(text: str, chars_per_second: float = CODE_CHARS_PER_SECOND) -> float:
    return len(text) / chars_per_second + text.count("\n") * NEWLINE_PAUSE_SECONDS


def action_seconds(kind: str, *, typed: str = "", command: str = "") -> float:
    seconds = _ACTION_OVERHEAD[kind]
    if typed:
        seconds += typing_seconds(typed)
    if command:
        seconds += typing_seconds(command, TERMINAL_CHARS_PER_SECOND)
    return round(seconds, 2)


def beat_seconds(narration: float, action: float) -> float:
    """Instructors talk while they type, so narration and action mostly overlap."""
    return round(max(narration, action) + 0.3, 2)
