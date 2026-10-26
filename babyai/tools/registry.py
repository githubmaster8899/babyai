from babyai.safety.rules import RiskLevel
from babyai.tools.base import Tool, ToolResult


class SpeakTool(Tool):
    name = "speak"
    description = "Speak text aloud using the configured text-to-speech system if available."
    risk_level = RiskLevel.LOW

    def invoke(self, text: str) -> ToolResult:
        return ToolResult(True, f"Voice output requested: {text}", {"text": text})


class ListenTool(Tool):
    name = "listen"
    description = "Listen for input from the microphone when voice mode is available."
    risk_level = RiskLevel.LOW

    def invoke(self, timeout: int = 10) -> ToolResult:
        return ToolResult(True, "Voice input requested.", {"timeout": timeout})


class CaptureScreenTool(Tool):
    name = "capture_screen"
    description = "Capture a screenshot. This is a sensitive action and should require approval."
    risk_level = RiskLevel.CRITICAL

    def invoke(self, path: str = "screenshot.png") -> ToolResult:
        return ToolResult(False, "Screen capture is not available in this environment.", {"path": path})


class GetClipboardTool(Tool):
    name = "get_clipboard"
    description = "Read clipboard contents."
    risk_level = RiskLevel.HIGH

    def invoke(self) -> ToolResult:
        return ToolResult(False, "Clipboard access is not available in this environment.")


class SetClipboardTool(Tool):
    name = "set_clipboard"
    description = "Write text to the system clipboard."
    risk_level = RiskLevel.HIGH

    def invoke(self, text: str) -> ToolResult:
        return ToolResult(False, "Clipboard writing is not available in this environment.", {"text": text})
