from __future__ import annotations

from typing import Any

from babyai.tools.base import Tool
from babyai.tools.filesystem import (
    EditFileTool,
    FindFilesTool,
    ListDirectoryTool,
    ReadFileTool,
    SearchFilesTool,
    WriteFileTool,
)
from babyai.tools.interaction import AskUserTool, NotifyTool
from babyai.tools.memory import ForgetMemoryTool, RecallMemoryTool, RememberFactTool
from babyai.tools.shell import RunShellTool
from babyai.tools.system import GetSystemInfoTool, ManageProcessesTool
from babyai.tools.voice import CaptureScreenTool, GetClipboardTool, ListenTool, SetClipboardTool, SpeakTool
from babyai.tools.web import FetchURLTool, WebSearchTool


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def get_openai_tools(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self.tools.values()]

    def call(self, name: str, arguments: dict[str, Any] | None = None, interactive: bool = True, user_confirmation: bool | None = None) -> Any:
        tool = self.get(name)
        if not tool:
            return {"ok": False, "content": f"Unknown tool: {name}"}
        args = arguments or {}
        if user_confirmation is not None:
            if not user_confirmation:
                return {"ok": False, "content": f"User rejected {name}."}
        return tool.invoke(**args)


def build_default_registry(memory_store=None, config: dict[str, Any] | None = None) -> ToolRegistry:
    registry = ToolRegistry()
    tool_instances = [
        ReadFileTool(memory_store, config),
        WriteFileTool(memory_store, config),
        EditFileTool(memory_store, config),
        ListDirectoryTool(memory_store, config),
        FindFilesTool(memory_store, config),
        SearchFilesTool(memory_store, config),
        RunShellTool(memory_store, config),
        GetSystemInfoTool(memory_store, config),
        ManageProcessesTool(memory_store, config),
        WebSearchTool(memory_store, config),
        FetchURLTool(memory_store, config),
        RememberFactTool(memory_store, config),
        RecallMemoryTool(memory_store, config),
        ForgetMemoryTool(memory_store, config),
        AskUserTool(memory_store, config),
        NotifyTool(memory_store, config),
        SpeakTool(memory_store, config),
        ListenTool(memory_store, config),
        CaptureScreenTool(memory_store, config),
        GetClipboardTool(memory_store, config),
        SetClipboardTool(memory_store, config),
    ]
    for tool in tool_instances:
        registry.register(tool)
    return registry
