"""HumanTypingEngine: turn text into keystrokes with natural, reproducible timing.

The engine only plans; `automation.macos` performs the keystrokes. Planning is deterministic for a
given seed, so the same script produces the same timing (and the timeline can be computed before
recording).
"""

import random
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

TypingMode = Literal["code", "prose", "terminal"]
_PUNCTUATION = set(".,;:!?")


class TypingSpeed(StrEnum):
    SLOW = "slow"
    NORMAL = "normal"
    FAST = "fast"


class TypingProfile(BaseModel):
    code_chars_per_second: float = Field(default=7.0, gt=0)
    prose_chars_per_second: float = Field(default=9.0, gt=0)
    terminal_chars_per_second: float = Field(default=8.0, gt=0)
    variation: float = Field(default=0.20, ge=0, lt=1, description="± fraction of each delay.")
    punctuation_pause: float = Field(default=0.12, ge=0)
    newline_pause: float = Field(default=0.25, ge=0)
    section_pause: float = Field(default=1.0, ge=0, description="Extra pause at a blank line.")
    indent_speedup: float = Field(
        default=4.0, ge=1, description="Leading indentation is typed this many times faster."
    )

    @classmethod
    def preset(cls, speed: TypingSpeed) -> "TypingProfile":
        return {
            TypingSpeed.SLOW: cls(
                code_chars_per_second=4.5, prose_chars_per_second=6, terminal_chars_per_second=5,
                newline_pause=0.4, section_pause=1.4,
            ),
            TypingSpeed.NORMAL: cls(),
            TypingSpeed.FAST: cls(
                code_chars_per_second=14, prose_chars_per_second=16, terminal_chars_per_second=15,
                punctuation_pause=0.05, newline_pause=0.12, section_pause=0.5,
            ),
        }[speed]  # fmt: skip


class Keystroke(BaseModel):
    """One character to type ("\\n" = Return) and how long to wait before typing it."""

    char: str = Field(min_length=1, max_length=1)
    delay_before: float = Field(ge=0)


class HumanTypingEngine:
    def __init__(self, profile: TypingProfile | None = None, seed: int | None = 0) -> None:
        self.profile = profile or TypingProfile()
        self._seed = seed

    def _speed(self, mode: TypingMode) -> float:
        return {
            "code": self.profile.code_chars_per_second,
            "prose": self.profile.prose_chars_per_second,
            "terminal": self.profile.terminal_chars_per_second,
        }[mode]

    def plan(self, text: str, mode: TypingMode = "code") -> list[Keystroke]:
        rng = random.Random(self._seed)
        base = 1.0 / self._speed(mode)
        p = self.profile
        strokes: list[Keystroke] = []
        at_line_start = True
        previous = ""
        for char in text.replace("\r\n", "\n").replace("\t", "    "):
            delay = base * rng.uniform(1 - p.variation, 1 + p.variation)
            if at_line_start and char == " ":
                delay /= p.indent_speedup  # indentation is a quick burst, like hitting Tab
            if previous in _PUNCTUATION and char == " ":
                delay += p.punctuation_pause
            if previous == "\n":
                delay += p.newline_pause
                if char == "\n":
                    delay += p.section_pause
            strokes.append(Keystroke(char=char, delay_before=round(delay, 4)))
            at_line_start = char == "\n" or (at_line_start and char == " ")
            previous = char
        return strokes

    def duration(self, text: str, mode: TypingMode = "code") -> float:
        return round(sum(k.delay_before for k in self.plan(text, mode)), 3)
