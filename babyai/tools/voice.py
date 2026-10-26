from babyai.safety.rules import RiskLevel
from babyai.tools.base import Tool, ToolResult


class AskUserTool(Tool):
    name = "ask_user"
    description = "Ask the user for clarification or more input."
    risk_level = RiskLevel.LOW

    def invoke(self, question: str) -> ToolResult:
        return ToolResult(True, question, {"question": question})


class NotifyTool(Tool):
    name = "notify"
    description = "Send a short user-visible notification."
    risk_level = RiskLevel.LOW

    def invoke(self, message: str, title: str = "BabyAI") -> ToolResult:
        return ToolResult(True, f"{title}: {message}", {"message": message, "title": title})
