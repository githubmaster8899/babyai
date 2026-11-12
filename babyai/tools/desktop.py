"""The window the user has in front, as a tool he can reach when they ask about it.

The reading itself is in babyai/desktop.py (best effort across Hyprland, kdotool, X11 and Sway). It is
rated MEDIUM in the safety rules: a window title can contain anything, so the strict gate asks before
he looks, and nothing is stored afterwards.
"""

from __future__ import annotations

from typing import Any

from babyai import desktop
from babyai.tools.base import Tool, ToolResult


class GetActiveWindowTool(Tool):
    name = "get_active_window"
    description = (
        "Read the title and app name of the window the user has in front. Use it only when they ask what you can see, "
        "what they are working on, or to make sense of a window they mention; it reads nothing else and stores nothing. "
        "If the desktop does not allow it, say so plainly."
    )

    def invoke(self) -> ToolResult:
        info = desktop.read_active_window()
        if not info or not (info.get("title") or info.get("app")):
            return ToolResult(False, "This desktop won't tell me which window is in front (no supported window tool, "
                                     "or the session hides it). I can still help if you tell me what you're looking at.")
        title = info.get("title") or "(no title)"
        app = info.get("app") or "unknown app"
        return ToolResult(True, f"The window in front is {app}: {title}", dict(info))

    def preview(self, **_: Any) -> dict[str, Any] | None:
        return {"active window": "the title and app name of the window in front"}
