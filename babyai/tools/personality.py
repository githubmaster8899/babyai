"""The user's view of his manner sliders and of what he knows about them.

The work is in babyai/personality.py, babyai/habits and the memory store; this is only the tool
the model reaches. ``about`` gathers the three things he is allowed to say about the user: the
sliders, the short habit lines, and facts they asked him to remember. It never reads a
conversation back out and never shows counts of individual messages.
"""

from __future__ import annotations

from typing import Any

from babyai.tools.base import Tool, ToolResult

MAX_FACTS = 20


class PersonalityTool(Tool):
    name = "personality"
    description = (
        "Show or change how you sound, and say what you know about the user. "
        "Actions: show - the four manner sliders and their values; about - the sliders, what you have learned "
        "about their habits (a few plain lines), and the facts they asked you to remember; set - move one slider "
        "(slider: playfulness, warmth, curiosity or verbosity; value: 1 to 5); reset - back to the starting values. "
        "Use set only when the user asks you to change how you sound. Use about only when they ask what you know about them."
    )

    def __init__(self, memory_store: Any = None, config: dict[str, Any] | None = None,
                 personality: Any = None, habits: Any = None):
        super().__init__(memory_store, config)
        self.personality = personality
        self.habits = habits

    def invoke(self, action: str = "show", slider: str = "", value: int = 0) -> ToolResult:
        name = (action or "").strip().lower()
        if self.personality is None:
            return ToolResult(False, "Personality sliders are turned off ([personality] enabled = false).")
        try:
            if name == "show":
                return ToolResult(True, self.personality.show())
            if name == "about":
                return ToolResult(True, self._about())
            if name == "set":
                return ToolResult(True, self.personality.set(slider, value), self.personality.values())
            if name == "reset":
                return ToolResult(True, self.personality.reset(), self.personality.values())
        except ValueError as exc:
            return ToolResult(False, str(exc))
        return ToolResult(False, f"{action!r} isn't an action of {self.name}. Actions: show, about, set, reset.")

    def _about(self) -> str:
        out = [self.personality.show(), "", "What I have noticed about you:"]
        lines: list[str] = []
        if self.habits is not None:
            try:
                lines = self.habits.profile()
            except Exception:
                lines = []
        if lines:
            out += [f"- {line}" for line in lines]
        else:
            out.append("- Not enough yet; I only draw conclusions after a while.")
        facts: list[dict[str, Any]] = []
        if self.memory_store is not None:
            try:
                facts = self.memory_store.recent_facts(MAX_FACTS)
            except Exception:
                facts = []
        if facts:
            out.append("")
            out.append("Things you asked me to remember:")
            out += [f"- {item['fact']}" for item in facts]
        else:
            out.append("")
            out.append("You haven't asked me to remember anything yet.")
        out.append("")
        out.append("All of it is kept on your computer and you can wipe it: the sliders and habits have files of their own, "
                   "and the facts are in your memory database.")
        return "\n".join(out)

    def preview(self, action: str = "show", slider: str = "", value: int = 0, **_: Any) -> dict[str, Any] | None:
        if self.personality is None:
            return None
        return self.personality.preview(action, slider=slider, value=value)
