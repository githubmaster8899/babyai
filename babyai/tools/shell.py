import os
import platform

from babyai.tools.base import Tool, ToolResult


class GetSystemInfoTool(Tool):
    name = "get_system_info"
    description = "Return basic system information for the current machine."

    def invoke(self) -> ToolResult:
        info = {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "python_version": platform.python_version(),
            "cwd": os.getcwd(),
        }
        return ToolResult(True, str(info), info)


class ManageProcessesTool(Tool):
    name = "manage_processes"
    description = "List or terminate processes when explicitly approved."

    def invoke(self, action: str = "list", pid: int | None = None, signal: str = "SIGTERM") -> ToolResult:
        try:
            import psutil
        except Exception:
            return ToolResult(False, "Process management is unavailable because psutil is not installed.")

        if action == "list":
            processes = []
            for proc in psutil.process_iter(["pid", "name", "status"]):
                try:
                    processes.append({
                        "pid": proc.info["pid"],
                        "name": proc.info["name"],
                        "status": proc.info["status"],
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return ToolResult(True, str(processes[:20]), {"processes": processes[:20]})

        if action == "kill" and pid is not None:
            proc = psutil.Process(pid)
            proc.terminate()
            return ToolResult(True, f"Terminated process {pid}", {"pid": pid})

        return ToolResult(False, "Unsupported process action.")
