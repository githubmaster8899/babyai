from babyai.tools.base import Tool, ToolResult


class AskUserTool(Tool):
    name = "ask_user"
    description = "Ask the user a clarifying question."

    def invoke(self, question: str) -> ToolResult:
        return ToolResult(True, question, {"question": question})


class NotifyTool(Tool):
    name = "notify"
    description = "Show a short notification to the user."

    def invoke(self, message: str, title: str = "BabyAI") -> ToolResult:
        return ToolResult(True, f"{title}: {message}", {"message": message, "title": title})
