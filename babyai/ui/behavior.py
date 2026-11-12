"""Display-free timing and state rules for the desktop overlay.

Nothing here imports Tk, so the rules can be unit-tested without a display and
``import babyai.ui.behavior`` works on machines where Tk is not installed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

THINK_AFTER_S = 15.0  # show the "THINKING… N s" counter once a reply takes this long
SLEEP_AFTER_S = 600.0  # idle time before the figure falls asleep (0 turns auto-sleep off)
BUBBLE_HIDE_S = 10.0  # the speech bubble hides this long after she has finished talking (0 keeps it up)
WAKE_RADIUS_PX = 150.0  # a pointer that moves this close to the figure's head wakes it
LOOK_RADIUS_PX = 250.0  # the eyes follow the pointer while it is within this distance of the head
LOOK_REACH_PX = (6, 4)  # furthest the eyes slide from centre, sideways and up/down
LOOK_FULL_AT_PX = 80.0  # from this distance on, the eyes are turned all the way
HIDE_AFTER_S = 180.0  # idle time before she plays hide-and-seek at the screen edge
PEEK_EVERY_S = 12.0  # while hidden, how often she peeks back out
PEEK_FOR_S = 2.5  # how long one peek lasts
HIDE_FRACTION = 0.46  # how much of the window slips off the edge (0.46 leaves her face showing)
PEEK_FRACTION = 0.25  # how much slips off during a peek


def thinking_label(elapsed_s: float, after_s: float = THINK_AFTER_S) -> str | None:
    """Text for the waiting counter, or None while the wait is still short.

    The number counts up in whole seconds: "THINKING… 15 s", "THINKING… 16 s", ...
    """
    if elapsed_s < after_s:
        return None
    return f"THINKING… {int(elapsed_s)} s"


class SleepClock:
    """Idle tracking and the asleep/awake state of the figure.

    Time is always passed in (never read here), so tests can step it freely.
    """

    def __init__(self, now: float, sleep_after_s: float = SLEEP_AFTER_S, wake_radius_px: float = WAKE_RADIUS_PX):
        self.sleep_after_s = sleep_after_s
        self.wake_radius_px = wake_radius_px
        self.last_activity = now
        self.asleep = False
        self.manual = False  # put to sleep on purpose (menu): only a click, key or the menu wakes it

    def sleep(self) -> None:
        """Put the figure to sleep on purpose. A passing pointer will not wake it."""
        self.asleep = True
        self.manual = True

    def touch(self, now: float) -> bool:
        """Something happened (a click, a key, a reply). Returns True if it woke the figure."""
        was_asleep = self.asleep
        self.last_activity = now
        self.asleep = False
        self.manual = False
        return was_asleep

    def tick(self, now: float, pointer_dist: float | None = None, pointer_moved: bool = False) -> bool:
        """Advance the clock; returns whether the figure is asleep afterwards.

        ``pointer_dist`` is the pointer's distance in pixels from the head (None if unknown).
        A pointer that *moves* within the wake radius counts as activity and wakes the figure;
        one that merely rests there does not, otherwise a parked mouse would keep waking it.
        """
        near = pointer_dist is not None and pointer_dist <= self.wake_radius_px
        if near and pointer_moved and not self.manual:
            self.touch(now)
        elif not self.asleep and self.sleep_after_s > 0 and now - self.last_activity >= self.sleep_after_s:
            self.asleep = True
        return self.asleep


def eye_offset(
    dx: float,
    dy: float,
    radius: float = LOOK_RADIUS_PX,
    reach: tuple[int, int] = LOOK_REACH_PX,
    full_at: float = LOOK_FULL_AT_PX,
) -> tuple[int, int]:
    """How far to slide the eyes (in pixels) toward a pointer at (dx, dy) from the head.

    Outside ``radius`` the eyes stay centred. Closer in they turn toward the pointer, easing
    from centred (pointer on the head) to the full ``reach`` once it is ``full_at`` away.
    """
    dist = math.hypot(dx, dy)
    if dist == 0 or dist > radius:
        return (0, 0)
    strength = min(1.0, dist / full_at)
    return (round(dx / dist * strength * reach[0]), round(dy / dist * strength * reach[1]))


@dataclass(frozen=True)
class UiTimings:
    think_after_s: float = THINK_AFTER_S
    sleep_after_s: float = SLEEP_AFTER_S
    wake_radius_px: float = WAKE_RADIUS_PX
    look_radius_px: float = LOOK_RADIUS_PX
    bubble_hide_s: float = BUBBLE_HIDE_S


def timings_from_config(config: object) -> UiTimings:
    """Read the ``[ui]`` section of the BabyAI config.

    Anything missing, not a plain number, negative or not finite falls back to the default,
    so a typo in config.toml can never break the overlay.
    """
    section = config.get("ui") if isinstance(config, dict) else None
    if not isinstance(section, dict):
        return UiTimings()
    defaults = UiTimings()
    values = {}
    for name in ("think_after_s", "sleep_after_s", "wake_radius_px", "look_radius_px", "bubble_hide_s"):
        value = section.get(name)
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
        values[name] = float(value) if ok else getattr(defaults, name)
    return UiTimings(**values)


def needs_typing_box(setting: object, windowing: str, env: Mapping[str, str]) -> bool:
    """Should typing go through the box? ``windowing`` is Tk's windowing system ("x11", "win32", "aqua")."""
    choice = str(setting or "auto").strip().lower()
    if choice in ("box", "popup"):
        return True
    if choice in ("bubble", "inline"):
        return False
    wayland = env.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland" or bool(env.get("WAYLAND_DISPLAY"))
    return windowing == "x11" and wayland


def cursor_line(lengths: Sequence[int], shown: int) -> int:
    """Index of the line being typed when ``shown`` letters of a reply are out. Each line counts its letters plus
    one for the break after it (the same count the reveal uses), and the last line once everything is out."""
    start = 0
    for index, length in enumerate(lengths):
        if shown < start + length + 1:
            return index
        start += length + 1
    return max(0, len(lengths) - 1)


def scroll_top(total: int, visible: int, cursor: int, user_top: int | None = None) -> int:
    """First line to show when a reply of ``total`` lines is shown ``visible`` lines at a time.

    By default the view follows the line being typed (``cursor``), so the newest text is always on screen. Once the
    reader has scrolled (``user_top``) it stays where they put it. Always within the reply."""
    last = max(0, total - visible)
    if user_top is not None:
        return min(max(0, user_top), last)
    return min(max(0, cursor - visible + 1), last)


class BubbleTimer:
    """Says when the speech bubble should go away: ``after_s`` seconds after she has finished talking.

    Time is passed in, never read here. It does not run while she is busy (thinking, typing out a reply, being typed to,
    asking something) or after the bubble is already hidden, and ``touch`` (the reader hovering or scrolling) starts it over."""

    def __init__(self, after_s: float) -> None:
        self._after = after_s
        self._due: float | None = None

    def touch(self) -> None:
        self._due = None

    def update(self, now: float, busy: bool, hidden: bool) -> bool:
        """Call every tick; True when the bubble should be hidden now."""
        if self._after <= 0 or busy or hidden:
            self._due = None
            return False
        if self._due is None:
            self._due = now + self._after
            return False
        return now >= self._due


@dataclass(frozen=True)
class HideTimings:
    hide_after_s: float = HIDE_AFTER_S
    peek_every_s: float = PEEK_EVERY_S
    peek_for_s: float = PEEK_FOR_S
    hide_fraction: float = HIDE_FRACTION
    peek_fraction: float = PEEK_FRACTION


def _finite_number(section: Mapping[str, object], name: str, default: float, minimum: float) -> float:
    value = section.get(name)
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= minimum
    return float(value) if ok else default


def hide_timings_from_config(config: object) -> HideTimings | None:
    """Read the hide-and-seek settings from ``[ui]``; None when the user has turned the game off.

    Missing or nonsensical values fall back to the defaults, and a typo can never break the overlay. The game is on by
    default: it only starts after a long idle spell, and any click or key brings her straight back."""
    section = config.get("ui") if isinstance(config, dict) else None
    if not isinstance(section, dict):
        return HideTimings()
    enabled = section.get("hide_and_seek", True)
    if enabled is False or str(enabled).strip().lower() in ("false", "no", "off", "0"):
        return None
    defaults = HideTimings()
    return HideTimings(
        hide_after_s=_finite_number(section, "hide_after_s", defaults.hide_after_s, 0.0),
        peek_every_s=_finite_number(section, "peek_every_s", defaults.peek_every_s, 0.5),
        peek_for_s=_finite_number(section, "peek_for_s", defaults.peek_for_s, 0.2),
    )


class HideAndSeek:
    """When she slips off the screen edge to play, peeks back, and returns when the user finds her.

    Display-free: the app asks for the mode each tick and moves the window; nothing here knows about Tk. ``busy``
    means she is thinking, typing, waiting on an answer, or already talking, and it keeps her out where the user can
    see her. Any click or key calls ``attention`` and brings her back."""

    OUT = "out"
    HIDDEN = "hidden"
    PEEKING = "peeking"

    def __init__(self, now: float, timings: HideTimings | None = None):
        self.timings = timings or HideTimings()
        self.last_attention = now
        self.mode = self.OUT
        self._next_peek = 0.0
        self._peek_until = 0.0

    def attention(self, now: float) -> bool:
        """The user called or found her: come back out. True if she had been hiding or peeking."""
        was_hiding = self.mode != self.OUT
        self.mode = self.OUT
        self.last_attention = now
        self._next_peek = self._peek_until = 0.0
        return was_hiding

    def update(self, now: float, busy: bool = False) -> str:
        """Call every tick; returns the mode she should be in now."""
        if busy:
            self.mode = self.OUT
            self.last_attention = now
            return self.mode
        if self.mode == self.OUT:
            if now - self.last_attention >= self.timings.hide_after_s:
                self.mode = self.HIDDEN
                self._next_peek = now + self.timings.peek_every_s
        elif self.mode == self.HIDDEN:
            if now >= self._next_peek:
                self.mode = self.PEEKING
                self._peek_until = now + self.timings.peek_for_s
        elif now >= self._peek_until:
            self.mode = self.HIDDEN
            self._next_peek = now + self.timings.peek_every_s
        return self.mode

    def fraction(self) -> float:
        """How much of the window's width should be off the screen edge in the current mode."""
        if self.mode == self.PEEKING:
            return self.timings.peek_fraction
        if self.mode == self.HIDDEN:
            return self.timings.hide_fraction
        return 0.0
