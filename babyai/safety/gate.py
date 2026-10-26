from __future__ import annotations

from enum import Enum


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


TOOL_RISK_LEVELS = {
    "read_file": RiskLevel.LOW,
    "write_file": RiskLevel.MEDIUM,
    "edit_file": RiskLevel.MEDIUM,
    "list_directory": RiskLevel.LOW,
    "find_files": RiskLevel.LOW,
    "search_files": RiskLevel.LOW,
    "run_shell": RiskLevel.HIGH,
    "get_system_info": RiskLevel.LOW,
    "manage_processes": RiskLevel.HIGH,
    "web_search": RiskLevel.LOW,
    "fetch_url": RiskLevel.LOW,
    "remember_fact": RiskLevel.LOW,
    "recall_memory": RiskLevel.LOW,
    "forget_memory": RiskLevel.MEDIUM,
    "ask_user": RiskLevel.LOW,
    "notify": RiskLevel.LOW,
    "speak": RiskLevel.LOW,
    "listen": RiskLevel.LOW,
    "capture_screen": RiskLevel.CRITICAL,
    "get_clipboard": RiskLevel.HIGH,
    "set_clipboard": RiskLevel.HIGH,
}


DANGEROUS_PATTERNS = (
    "rm -rf /",
    "rm -rf /tmp",
    "mkfs",
    "format c:",
    "dd if=/dev/zero",
    "shutdown -h now",
    "reboot",
    "curl .* | sh",
    "wget .* | sh",
    "chmod -R 777 /",
    "chown -R root /",
)


def is_dangerous_command(command: str) -> bool:
    lowered = command.lower()
    for pattern in DANGEROUS_PATTERNS:
        if pattern.lower() in lowered:
            return True
    return False


def get_risk_level(tool_name: str, arguments: dict | None = None) -> RiskLevel:
    base = TOOL_RISK_LEVELS.get(tool_name, RiskLevel.LOW)
    args = arguments or {}
    if tool_name == "run_shell":
        command = str(args.get("command", "")).lower()
        if is_dangerous_command(command):
            return RiskLevel.CRITICAL
        if "sudo" in command or "rm -rf" in command:
            return RiskLevel.CRITICAL
        if "curl" in command or "wget" in command:
            return RiskLevel.HIGH
    if tool_name == "capture_screen":
        return RiskLevel.CRITICAL
    if tool_name in {"get_clipboard", "set_clipboard"}:
        return RiskLevel.HIGH
    return base
