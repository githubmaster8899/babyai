from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConversationTurn:
    role: str
    content: str


class ConversationContext:
    def __init__(self, system_prompt: str):
        self.system_prompt = system_prompt
        self.history: list[ConversationTurn] = []

    def add_user_message(self, content: str) -> None:
        self.history.append(ConversationTurn("user", content))

    def add_assistant_message(self, content: str) -> None:
        self.history.append(ConversationTurn("assistant", content))

    def build_messages(self) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [{"role": "system", "content": self.system_prompt}]
        for turn in self.history:
            messages.append({"role": turn.role, "content": turn.content})
        return messages

    def reset(self) -> None:
        self.history.clear()
