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


# The self-upgrade tools (babyai/tools/upgrade.py) take an ``action``, and what it does decides how risky the call is.
# Anything that changes him or his machine is the user's call whatever the safety level (see SafetyGate.check);
# looking, and working on a branch that is not live, is not.
UPGRADE_ACTION_RISK: dict[str, dict[str, RiskLevel]] = {
    "upgrade_code": {
        "status": RiskLevel.LOW, "start": RiskLevel.LOW, "write": RiskLevel.LOW, "replace": RiskLevel.LOW,
        "diff": RiskLevel.LOW, "discard": RiskLevel.LOW,
        "test": RiskLevel.HIGH,  # runs the code he has just written
        "apply": RiskLevel.HIGH,  # makes it live
    },
    "upgrade_version": {
        "check": RiskLevel.LOW,
        "update": RiskLevel.HIGH, "rollback": RiskLevel.HIGH, "restart": RiskLevel.HIGH,
    },
    "upgrade_model": {
        "list": RiskLevel.LOW,
        "pull": RiskLevel.HIGH, "switch": RiskLevel.HIGH,
    },
    "upgrade_ideas": {  # his own suggestions: they change nothing themselves (accepting only records the user's yes)
        "list": RiskLevel.LOW, "suggest": RiskLevel.LOW, "accept": RiskLevel.LOW, "dismiss": RiskLevel.LOW,
        "pause": RiskLevel.MEDIUM, "resume": RiskLevel.MEDIUM,
    },
    "habit_profile": {"show": RiskLevel.LOW, "pause": RiskLevel.MEDIUM, "resume": RiskLevel.MEDIUM, "forget": RiskLevel.HIGH},
    "upgrade_notes": {
        "show": RiskLevel.LOW, "show_skill": RiskLevel.LOW,
        "add_note": RiskLevel.MEDIUM, "remove_note": RiskLevel.MEDIUM,
        "save_skill": RiskLevel.MEDIUM, "delete_skill": RiskLevel.MEDIUM,
    },
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
    if tool_name in UPGRADE_ACTION_RISK:
        return UPGRADE_ACTION_RISK[tool_name].get(str(args.get("action", "")).strip().lower(), RiskLevel.HIGH)
    if tool_name == "capture_screen":
        return RiskLevel.CRITICAL
    if tool_name in {"get_clipboard", "set_clipboard"}:
        return RiskLevel.HIGH
    return base
