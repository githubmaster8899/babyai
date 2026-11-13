"""The tools for reminders and timers. The work is in babyai/reminders.py; a reminder is only ever shown to the user, never run."""

from __future__ import annotations

from typing import Any

from babyai.reminders import ReminderError, when_text
from babyai.tools.base import Tool, ToolResult


class _ReminderTool(Tool):
    def __init__(self, memory_store: Any = None, config: dict[str, Any] | None = None, reminders: Any = None):
        super().__init__(memory_store, config)
        self.reminders = reminders


class SetReminderTool(_ReminderTool):
    name = "set_reminder"
    description = (
        "Remind the user of something later, or run a timer. Use it when they say things like 'remind me to ...', 'in 10 minutes ...' "
        "or 'set a timer'. text is what to remind them of (for a plain timer, 'timer is up'). Give EITHER minutes (from now, e.g. 25) "
        "OR at (a clock time like '15:30' or '3:30 pm', meaning the next time it is that time; or 'YYYY-MM-DD HH:MM' for a date you "
        "were told). You don't know today's date, so prefer minutes or a clock time. It pops up on their screen when it is due, "
        "while you are running."
    )

    def invoke(self, text: str, minutes: float = 0, at: str = "") -> ToolResult:
        try:
            item = self.reminders.add(text, minutes, at)
        except ReminderError as exc:
            return ToolResult(False, str(exc))
        told = when_text(item["due"], self.reminders.now())
        if item.get("duplicate"):
            return ToolResult(True, f"Already set: \"{item['text']}\" at {told} (reminder {item['id']}).", {"id": item["id"], "duplicate": True})
        return ToolResult(True, f"Set: \"{item['text']}\" at {told} (reminder {item['id']}).", {"id": item["id"], "due": item["due"]})


class ListRemindersTool(_ReminderTool):
    name = "list_reminders"
    description = "List the reminders and timers that are waiting, with their ids and when each is due."

    def invoke(self) -> ToolResult:
        items = self.reminders.pending()
        if not items:
            return ToolResult(True, "No reminders are waiting.", {"reminders": []})
        now = self.reminders.now()
        lines = [f"{item['id']}: \"{item['text']}\" at {when_text(item['due'], now)}" for item in items]
        return ToolResult(True, "\n".join(lines), {"reminders": [{"id": i["id"], "text": i["text"], "due": i["due"]} for i in items]})


class CancelReminderTool(_ReminderTool):
    name = "cancel_reminder"
    description = "Cancel one waiting reminder or timer by its id (list_reminders shows the ids)."

    def invoke(self, id: int) -> ToolResult:
        try:
            item = self.reminders.cancel(id)
        except ReminderError as exc:
            return ToolResult(False, str(exc))
        return ToolResult(True, f"Cancelled: \"{item['text']}\".", {"id": item["id"]})

    def preview(self, id: Any = None, **_: Any) -> dict[str, Any] | None:
        item = self.reminders.find(id)
        if item is None:
            return {"cancel": f"nothing (there is no reminder {id})"}
        return {"cancel": f"\"{item['text'][:80]}\"", "was due": when_text(item["due"], self.reminders.now())}
