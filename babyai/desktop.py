"""What window is in front, for the desktop companion - best effort, on X11 and Wayland.

There is no portable API for this, so a few small tools are tried in turn (Hyprland, KDE's kdotool,
X11's xdotool, Sway's swaymsg). Whichever answers first wins; if none is installed or the session
doesn't expose it, the answer is ``None`` and the caller says so instead of guessing.

Only the title and the app name are read, nothing is stored, and nothing here runs a shell.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any, Callable

TIMEOUT_S = 1.5


def _output(command: list[str], run: Callable[..., Any]) -> str:
    try:
        finished = run(command, capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    if getattr(finished, "returncode", 1) != 0:
        return ""
    return str(getattr(finished, "stdout", "") or "").strip()


def _hyprland(run: Callable[..., Any]) -> dict[str, str] | None:
    text = _output(["hyprctl", "activewindow", "-j"], run)
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    title = str(data.get("title") or "").strip()
    app = str(data.get("class") or data.get("initialClass") or "").strip()
    return {"app": app, "title": title, "backend": "hyprctl"} if title or app else None


def _focused_sway_node(node: Any) -> dict[str, Any] | None:
    if not isinstance(node, dict):
        return None
    if node.get("focused"):
        return node
    for child in node.get("nodes", []) + node.get("floating_nodes", []):
        found = _focused_sway_node(child)
        if found is not None:
            return found
    return None


def _sway(run: Callable[..., Any]) -> dict[str, str] | None:
    text = _output(["swaymsg", "-t", "get_tree", "-r"], run)
    if not text:
        return None
    try:
        node = _focused_sway_node(json.loads(text))
    except ValueError:
        return None
    if not node:
        return None
    title = str(node.get("name") or "").strip()
    app = str(node.get("app_id") or "").strip()
    if not app:
        window = node.get("window_properties") or {}
        app = str(window.get("class") or window.get("instance") or "").strip()
    return {"app": app, "title": title, "backend": "swaymsg"} if title or app else None


def _xdotool_like(tool: str, run: Callable[..., Any]) -> dict[str, str] | None:
    title = _output([tool, "getactivewindow", "getwindowname"], run)
    app = _output([tool, "getactivewindow", "getwindowclassname"], run)
    return {"app": app, "title": title, "backend": tool} if title or app else None


def read_active_window(run: Callable[..., Any] | None = None,
                      which: Callable[[str], str | None] | None = None) -> dict[str, str] | None:
    """The focused window as ``{"app", "title", "backend"}``, or ``None`` when the desktop won't say."""
    run = run or subprocess.run
    which = which or shutil.which
    for command, reader in (
        ("hyprctl", _hyprland),
        ("kdotool", lambda r: _xdotool_like("kdotool", r)),
        ("xdotool", lambda r: _xdotool_like("xdotool", r)),
        ("swaymsg", _sway),
    ):
        if which(command):
            found = reader(run)
            if found is not None:
                return found
    return None
