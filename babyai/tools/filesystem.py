from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from babyai.safety.rules import RiskLevel


@dataclass
class ToolResult:
    ok: bool
    content: str
    data: dict[str, Any] = field(default_factory=dict)


class Tool:
    name: str = ""
    description: str = ""
    risk_level: RiskLevel = RiskLevel.LOW
    requires_confirmation: bool = False

    def __init__(self, memory_store=None, config: dict[str, Any] | None = None):
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

    def _validate_args(self, **kwargs: Any) -> dict[str, Any]:
        return kwargs
