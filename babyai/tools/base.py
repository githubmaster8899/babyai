from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from typing import Any

_JSON_TYPE_WORDS = {"bool": "boolean", "int": "integer", "float": "number", "str": "string"}


def _json_type(annotation: Any) -> str:
    """Map a parameter annotation (a type, or a string under ``from __future__ import annotations``)
    to a JSON-schema type name. Anything unrecognised is offered to the model as a string."""
    # ``str()`` rather than ``__name__``: on Python 3.14 ``int | None`` is a ``typing.Union`` whose
    # ``__name__`` is "Union", while its text still names the real type.
    text = annotation if isinstance(annotation, str) else str(annotation)
    match = re.search(r"\b(bool|int|float|str)\b", text)
    return _JSON_TYPE_WORDS[match.group(1)] if match else "string"


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

    def preview(self, **kwargs: Any) -> dict[str, Any] | None:
        """What the user should see when asked whether this call may run, if more than the raw arguments
        (for instance what a change would touch). Called with the same arguments as ``invoke``; must not change anything."""
        return None

    def parameters(self) -> dict[str, Any]:
        """JSON-schema for the arguments, read from the signature of ``invoke``."""
        properties: dict[str, Any] = {}
        required: list[str] = []
        for pname, param in inspect.signature(self.invoke).parameters.items():
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            prop: dict[str, Any] = {"type": _json_type(param.annotation)}
            if param.default is inspect.Parameter.empty:
                required.append(pname)
            elif param.default is not None:
                prop["default"] = param.default
            properties[pname] = prop
        return {"type": "object", "properties": properties, "required": required}

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters(),
            },
        }
