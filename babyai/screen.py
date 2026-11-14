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
import tempfile
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
    is a separate process, so a desktop that refuses screenshots ends with a plain None, not a crash.

    Each tool writes into a private scratch folder next to ``path`` and only a real, non-empty picture is
    moved into place, so a failed attempt never touches a file that was already at ``path``."""
    run = run or subprocess.run
    which = which or shutil.which
    absolute = os.path.abspath(os.path.expanduser(path))
    folder = os.path.dirname(absolute) or "."
    try:
        os.makedirs(folder, exist_ok=True)
        scratch = tempfile.mkdtemp(prefix=".babyai-shot-", dir=folder)
    except OSError:
        return None
    try:
        taken = os.path.join(scratch, "shot" + (os.path.splitext(absolute)[1] or ".png"))
        for command in _commands(taken, target):
            if not which(command[0]):
                continue
            if _quiet_run(command, run) and os.path.isfile(taken) and os.path.getsize(taken) > 0:
                try:
                    os.replace(taken, absolute)
                except OSError:
                    return None
                return absolute
            _forget(taken)  # whatever a failed attempt left behind must not pass for the next one's work
        return None
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _forget(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
