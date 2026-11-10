"""How the user sees and controls what he has learned about their habits. The work is in babyai/habits; the safety gate
(babyai/safety/rules.py: UPGRADE_ACTION_RISK) always asks before pausing, resuming or forgetting."""

from __future__ import annotations

from typing import Any

from babyai.tools.base import Tool, ToolResult


class HabitProfileTool(Tool):
    name = "habit_profile"
    description = (
        "What you have learned about the user's habits (topics, times of day, how they like replies) and the controls over it. "
        "Use it only when the user asks what you have learned about them, or asks you to pause, resume or forget it. "
        "Actions: show - tell them what you know and where it is kept; pause - stop learning and stop using it; "
        "resume - learn again; forget - delete everything."
    )

    def __init__(self, memory_store: Any = None, config: dict[str, Any] | None = None, habits: Any = None):
        super().__init__(memory_store, config)
        self.habits = habits

    def _handlers(self) -> dict[str, Any]:
        return {"show": self.habits.show, "pause": self.habits.pause, "resume": self.habits.resume, "forget": self.habits.forget}

    def invoke(self, action: str = "show") -> ToolResult:
        handlers = self._handlers()
        handler = handlers.get((action or "").strip().lower())
        if handler is None:
            return ToolResult(False, f"{action!r} isn't an action of {self.name}. Actions: {', '.join(handlers)}.")
        return ToolResult(True, handler())

    def preview(self, action: str = "show", **_: Any) -> dict[str, Any] | None:
        return self.habits.preview(action)
