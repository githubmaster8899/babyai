import subprocess

from babyai.tools.base import Tool, ToolResult


class RunShellTool(Tool):
    name = "run_shell"
    description = "Execute a shell command after safety checks."

    def invoke(self, command: str, timeout: int = 30, shell: bool = True) -> ToolResult:
        if "rm -rf /" in command.lower() or "mkfs" in command.lower() or "sudo rm" in command.lower():
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
