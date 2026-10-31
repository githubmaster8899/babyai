"""Display-free timing and state rules for the desktop overlay.

Nothing here imports Tk, so the rules can be unit-tested without a display and
``import babyai.ui.behavior`` works on machines where Tk is not installed.
"""

from __future__ import annotations

import math

THINK_AFTER_S = 15.0  # show the "THINKING… N s" counter once a reply takes this long
SLEEP_AFTER_S = 600.0  # idle time before the figure falls asleep (0 turns auto-sleep off)
WAKE_RADIUS_PX = 150.0  # a pointer that moves this close to the figure's head wakes it
LOOK_RADIUS_PX = 250.0  # the eyes follow the pointer while it is within this distance of the head
LOOK_REACH_PX = (6, 4)  # furthest the eyes slide from centre, sideways and up/down
LOOK_FULL_AT_PX = 80.0  # from this distance on, the eyes are turned all the way


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

    def touch(self, now: float) -> bool:
        """Something happened (a click, a key, a reply). Returns True if it woke the figure."""
        was_asleep = self.asleep
        self.last_activity = now
        self.asleep = False
        return was_asleep

    def tick(self, now: float, pointer_dist: float | None = None, pointer_moved: bool = False) -> bool:
        """Advance the clock; returns whether the figure is asleep afterwards.

        ``pointer_dist`` is the pointer's distance in pixels from the head (None if unknown).
        A pointer that *moves* within the wake radius counts as activity and wakes the figure;
        one that merely rests there does not, otherwise a parked mouse would keep waking it.
        """
        near = pointer_dist is not None and pointer_dist <= self.wake_radius_px
        if near and pointer_moved:
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
