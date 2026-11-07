"""Capri's body: which sprite cell to show, and when. Pure logic - no Tk, no clocks of its own.

This is a Python port of the animation rules in ``capri/cross/Pet.cs`` (clips, the idle
loop with its random blinks and glitches, the grin when she is petted, sleep and wake)
and ``capri/cross/Talk.cs`` (the mouth that follows the letters as they are typed).

The sprites and the behaviour they follow are Capri's, by Lachy, and are used under the
PolyForm Noncommercial License 1.0.0 - see ``capri/LICENSE.md`` and the root ``NOTICE``.
Required Notice: Copyright 2026 Lachy (https://github.com/lachydotmcg)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Clip:
    name: str  # also the file name: capri/assets/sprites/<name>.png
    cells: int  # frames laid out left to right in the strip
    fps: float


CLIPS: dict[str, Clip] = {
    clip.name: clip
    for clip in (
        Clip("idle", 16, 8),
        Clip("blink", 4, 8),
        Clip("blink_glitch", 4, 8),
        Clip("glitch", 10, 15),
        Clip("grin", 16, 8),
        Clip("sleep", 16, 6),
        Clip("wake", 13, 15),
        Clip("arrive", 14, 15),
        Clip("mouth", 5, 12),  # overlay: 0 closed, 1-2 small, 3 open, 4 wide
    )
}

BLINK_FROM = 9  # a blink replaces idle cells 9..12
GRIN_LOOPS = 2
GLITCH_EVERY_S = (30.0, 30.0)  # a quiet idle loop may glitch every 30 + [0, 30) seconds
PET_COOLDOWN_S = 20.0
PET_TURNS = 4  # direction changes within PET_WINDOW_S count as a stroke
PET_WINDOW_S = 1.5
PET_MIN_STEP_PX = 6

LETTERS_PER_SECOND = 28.0
MOUTH_RATE = 12.0

PET_LINES = (
    "Heh. Okay, I'll allow it.",
    "Pets logged. Morale up twelve percent.",
    "Careful, choom. I bite viruses.",
)


@dataclass(frozen=True)
class Frame:
    clip: str  # which strip to draw from
    cell: int
    breathe: float  # vertical offset in unscaled sprite pixels
    mouth: int  # mouth overlay cell, or -1 for none


def mouth_for(letter: str, step: int) -> int:
    """Mouth cell for one letter. Vowels open wide, m/b/p and non-letters close, the rest flap."""
    lower = letter.lower()
    if lower in "aeo":
        return 4 if letter.isupper() else 3
    if lower in "iuy":
        return 2
    if lower in "mbp" or not letter.isalpha():
        return 0
    return 1 if step % 2 == 1 else 2


def letters_revealed(elapsed: float, total: int) -> int:
    """How many letters of a reply have been typed after ``elapsed`` seconds."""
    return max(0, min(total, int(elapsed * LETTERS_PER_SECOND)))


def mouth_cell(text: str, elapsed: float) -> int:
    """Mouth cell while ``text`` is being typed out, -1 once it has all been said."""
    if elapsed < 0:
        return -1
    letter = int(elapsed * LETTERS_PER_SECOND)
    if letter >= len(text):
        return -1
    return mouth_for(text[letter], int(elapsed * MOUTH_RATE))


class Animator:
    """The state machine behind her idle loop. Feed it the time; it says which cell to draw."""

    def __init__(self, now: float, rng: random.Random | None = None, intro: bool = True):
        self._rng = rng or random.Random()
        self.clip = "arrive" if intro else "idle"
        self._start = now
        self._cycle = 0
        self._blink_with: str | None = None
        self._next_glitch = now + self._glitch_gap()

    def _glitch_gap(self) -> float:
        base, spread = GLITCH_EVERY_S
        return base + self._rng.random() * spread

    def play(self, name: str, now: float) -> None:
        self.clip = name
        self._start = now
        self._cycle = 0
        self._blink_with = None

    # ---- things that happen to her

    def set_asleep(self, asleep: bool, now: float) -> None:
        """Call every tick. She settles into sleep from idle, and wakes with the boot glitch."""
        if asleep and self.clip == "idle":
            self.play("sleep", now)
        elif not asleep and self.clip == "sleep":
            self.play("wake", now)

    def speak(self, now: float) -> None:
        """Talking cuts off whatever she was doing (as in Capri.Say)."""
        if self.clip in ("sleep", "wake", "glitch", "grin"):
            self.play("idle", now)

    def pet(self, now: float) -> None:
        if self.clip != "sleep":
            self.play("grin", now)

    # ---- what to draw

    def frame(self, now: float, quiet: bool = True, mouth: int = -1) -> Frame:
        """``quiet`` is False while she is talking or thinking (no glitch then); ``mouth`` is the
        mouth cell wanted by the current reply."""
        clip = CLIPS[self.clip]
        index = int((now - self._start) * clip.fps)
        finished = index >= clip.cells * (GRIN_LOOPS if self.clip == "grin" else 1)
        if self.clip in ("glitch", "wake", "arrive", "grin") and finished:
            self.play("idle", now)
            clip, index = CLIPS["idle"], 0

        if self.clip == "idle":
            cycle = index // clip.cells
            if cycle != self._cycle:
                self._cycle = cycle
                roll = self._rng.random()
                self._blink_with = "blink_glitch" if roll < 0.1 else "blink" if roll < 0.55 else None
                if now >= self._next_glitch and quiet:
                    self._next_glitch = now + self._glitch_gap()
                    self.play("glitch", now)
                    clip, index = CLIPS["glitch"], 0

        cell = index % clip.cells
        shown = self.clip
        if self.clip == "idle" and self._blink_with and BLINK_FROM <= cell < BLINK_FROM + CLIPS[self._blink_with].cells:
            shown = self._blink_with
            cell -= BLINK_FROM
        talking = mouth if self.clip == "idle" and shown != "blink_glitch" else -1
        return Frame(shown, cell, math.sin(now * 1.75) * 2.2, talking)


class PetDetector:
    """Spots a stroking motion: the pointer going back and forth across her body."""

    def __init__(self, min_step_px: float = PET_MIN_STEP_PX) -> None:
        self._min_step = min_step_px  # scale this with the sprite: Capri's 6 px is for her full-size frame
        self._x: float | None = None
        self._way = 0
        self._turns: list[float] = []
        self._last_pet = -math.inf

    def reset(self) -> None:
        self._turns.clear()
        self._way = 0

    def feed(self, x: float, now: float, over_body: bool, blocked: bool = False) -> bool:
        """Report a pointer position; True when this movement completes a stroke."""
        if blocked or not over_body:
            self.reset()
            return False
        if self._x is None:
            self._x = x
            return False
        dx = x - self._x
        if abs(dx) < self._min_step:
            return False
        way = 1 if dx > 0 else -1
        self._x = x
        if self._way and way != self._way:
            self._turns.append(now)
        self._way = way
        self._turns = [t for t in self._turns if now - t <= PET_WINDOW_S]
        if len(self._turns) < PET_TURNS or now - self._last_pet < PET_COOLDOWN_S:
            return False
        self.reset()
        self._last_pet = now
        return True


def pet_line(count: int, lines: Sequence[str] = PET_LINES) -> str:
    return lines[count % len(lines)]
