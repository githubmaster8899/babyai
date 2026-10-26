import os
import platform

from babyai.tools.base import Tool, ToolResult
from babyai.safety.rules import RiskLevel


class GetSystemInfoTool(Tool):
    name = "get_system_info"
    description = "Collect basic system information."
    risk_level = RiskLevel.LOW

    def invoke(self) -> ToolResult:
        info = {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "python_version": platform.python_version(),
            "cwd": os.getcwd(),
        }
        return ToolResult(True, str(info), info)


class ManageProcessesTool(Tool):
    name = "manage_processes"
    description = "List or manage running processes when explicitly approved."
    risk_level = RiskLevel.HIGH

    def invoke(self, action: str = "list", pid: int | None = None, signal: str = "SIGTERM") -> ToolResult:
        try:
            import psutil
        except Exception:
            return ToolResult(False, "Process management is unavailable because psutil is not installed.")

        if action == "list":
            processes = [
                {
                    "pid": p.pid,
                    "name": p.name(),
                    "status": p.status(),
                }
                for p in psutil.process_iter(['pid', 'name', 'status'])
            ]
            return ToolResult(True, str(processes[:20]), {"processes": processes[:20]})

        if action == "kill" and pid is not None:
            proc = psutil.Process(pid)
            proc.terminate()
            return ToolResult(True, f"Terminated process {pid}", {"pid": pid})

        return ToolResult(False, "Unsupported process action.")
