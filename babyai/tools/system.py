import subprocess

from babyai.tools.base import Tool, ToolResult
from babyai.safety.rules import RiskLevel, is_dangerous_command


class RunShellTool(Tool):
    name = "run_shell"
    description = "Run a shell command with explicit safety checks."
    risk_level = RiskLevel.HIGH

    def invoke(self, command: str, timeout: int = 30, shell: bool = True) -> ToolResult:
        if is_dangerous_command(command):
            return ToolResult(False, "Command denied by safety rules.")
        try:
            completed = subprocess.run(
                command,
                shell=shell,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            return ToolResult(
                completed.returncode == 0,
                output.strip() or "Command executed without output.",
                {"returncode": completed.returncode},
            )
        except subprocess.TimeoutExpired:
            return ToolResult(False, "Command timed out.")
        except Exception as exc:
            return ToolResult(False, f"Command failed: {exc}")
