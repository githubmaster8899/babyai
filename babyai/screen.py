"""Taking a screenshot with whatever the desktop already has - no extra Python dependency.

Wayland has no single screenshot API, so a few command-line tools are tried in turn: ``grim`` (Sway,
Hyprland and other wlroots desktops), KDE's ``spectacle``, ``gnome-screenshot``, then the X11 tools
``scrot`` and ImageMagick's ``import``. The first one that writes a non-empty file wins. Nothing is
uploaded here; the caller decides what to do with the file (see ``babyai/tools/screen.py``, which asks a
vision model and then deletes it).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Callable

TIMEOUT_S = 12


def _quiet_run(command: list[str], run: Callable[..., Any]) -> bool:
    try:
        finished = run(command, capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return getattr(finished, "returncode", 1) == 0


def _commands(path: str, target: str) -> list[list[str]]:
    if target == "window":
        return [
            ["spectacle", "-b", "-n", "-a", "-o", path],
            ["gnome-screenshot", "-w", "-f", path],
            ["scrot", "-u", "-o", path],
        ]
    return [
        ["grim", path],
        ["spectacle", "-b", "-n", "-f", "-o", path],
        ["gnome-screenshot", "-f", path],
        ["scrot", "-o", path],
        ["import", "-window", "root", path],
    ]


def capture_to(path: str, target: str = "screen", run: Callable[..., Any] | None = None,
               which: Callable[[str], str | None] | None = None) -> str | None:
    """Write a screenshot of the whole screen (or the focused window) to ``path``; return it, or None.

    ``target`` is ``"screen"`` or ``"window"``. Only tools already installed are tried, and each attempt
    is a separate process, so a desktop that refuses screenshots ends with a plain None, not a crash."""
    run = run or subprocess.run
    which = which or shutil.which
    absolute = os.path.abspath(os.path.expanduser(path))
    try:
        os.makedirs(os.path.dirname(absolute) or ".", exist_ok=True)
    except OSError:
        return None
    for command in _commands(absolute, target):
        if not which(command[0]):
            continue
        if os.path.exists(absolute):  # a leftover from an earlier attempt must not count as this one's work
            try:
                os.remove(absolute)
            except OSError:
                pass
        if _quiet_run(command, run) and os.path.exists(absolute) and os.path.getsize(absolute) > 0:
            return absolute
    return None
