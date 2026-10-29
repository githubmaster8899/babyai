from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    ok: bool
    content: str
    data: dict[str, Any] = field(default_factory=dict)


class Tool:
    name: str = ""
    description: str = ""

    def __init__(self, memory_store: Any = None, config: dict[str, Any] | None = None):
        self.memory_store = memory_store
        self.config = config or {}

    def invoke(self, **kwargs: Any) -> ToolResult:
        raise NotImplementedError

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }
