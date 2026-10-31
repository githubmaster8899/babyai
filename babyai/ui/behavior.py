"""Display-free timing and state rules for the desktop overlay.

Nothing here imports Tk, so the rules can be unit-tested without a display and
``import babyai.ui.behavior`` works on machines where Tk is not installed.
"""

from __future__ import annotations

THINK_AFTER_S = 15.0  # show the "THINKING… N s" counter once a reply takes this long


def thinking_label(elapsed_s: float, after_s: float = THINK_AFTER_S) -> str | None:
    """Text for the waiting counter, or None while the wait is still short.

    The number counts up in whole seconds: "THINKING… 15 s", "THINKING… 16 s", ...
    """
    if elapsed_s < after_s:
        return None
    return f"THINKING… {int(elapsed_s)} s"
