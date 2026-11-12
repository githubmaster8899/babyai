from babyai import screen
from babyai.tools.base import Tool, ToolResult


class SpeakTool(Tool):
    name = "speak"
    description = "Generate speech output if voice mode is available."

    def invoke(self, text: str) -> ToolResult:
        return ToolResult(True, f"Voice output requested: {text}", {"text": text})


class ListenTool(Tool):
    name = "listen"
    description = "Listen to the microphone for speech input when voice mode is enabled."

    def invoke(self, timeout: int = 10) -> ToolResult:
        return ToolResult(True, "Voice input requested.", {"timeout": timeout})


class CaptureScreenTool(Tool):
    name = "capture_screen"
    description = "Save a screenshot of the screen to a file the user names. This is a high-risk action."

    def invoke(self, path: str = "screenshot.png") -> ToolResult:
        saved = screen.capture_to(path)
        if saved is None:
            return ToolResult(False, "I couldn't take a screenshot. On Wayland I need grim or spectacle; on X11 scrot or "
                                     "ImageMagick's import. The desktop may also be refusing screen capture.",
                              {"path": path})
        return ToolResult(True, f"Saved a screenshot to {saved}.", {"path": saved})


class GetClipboardTool(Tool):
    name = "get_clipboard"
    description = "Read clipboard contents."

    def invoke(self) -> ToolResult:
        return ToolResult(False, "Clipboard access is unavailable in this environment.")


class SetClipboardTool(Tool):
    name = "set_clipboard"
    description = "Write text to the system clipboard."

    def invoke(self, text: str) -> ToolResult:
        return ToolResult(False, "Clipboard writing is unavailable in this environment.", {"text": text})
