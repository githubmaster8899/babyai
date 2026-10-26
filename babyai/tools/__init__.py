from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from babyai.safety.rules import RiskLevel, get_risk_level, is_dangerous_command


@dataclass
class AuditEntry:
    tool_name: str
    risk_level: str
    decision: str
    reason: str = ""
    user_confirmation: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class SafetyGate:
    def __init__(self, level: str = "strict"):
        self.level = level.lower()
        self.audit_log: list[AuditEntry] = []

    def check(self, tool_name: str, arguments: dict[str, Any] | None = None, interactive: bool = True) -> dict[str, Any]:
        args = arguments or {}
        risk_level = get_risk_level(tool_name, args)

        if tool_name == "run_shell":
            command = str(args.get("command", ""))
            if is_dangerous_command(command):
                self._record(tool_name, risk_level, "deny", "Dangerous shell command pattern detected", args)
                return {"allowed": False, "decision": "deny", "needs_confirmation": False, "risk_level": risk_level, "reason": "Dangerous shell command pattern detected"}

        if self._hard_deny(tool_name, args):
            self._record(tool_name, risk_level, "deny", "Hard deny rule matched", args)
            return {"allowed": False, "decision": "deny", "needs_confirmation": False, "risk_level": risk_level, "reason": "Hard deny rule matched"}

        if risk_level in {RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL}:
            if interactive:
                if self.level == "strict":
                    needs_confirmation = True
                elif self.level == "moderate" and risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
                    needs_confirmation = True
                else:
                    needs_confirmation = False
            else:
                needs_confirmation = True
        else:
            needs_confirmation = False

        if needs_confirmation:
            self._record(tool_name, risk_level, "needs_confirmation", "Confirmation required", args)
            return {"allowed": False, "decision": "needs_confirmation", "needs_confirmation": True, "risk_level": risk_level, "reason": "Confirmation required"}

        self._record(tool_name, risk_level, "allow", "No restriction triggered", args)
        return {"allowed": True, "decision": "allow", "needs_confirmation": False, "risk_level": risk_level, "reason": "No restriction triggered"}

    def approve(self, tool_name: str, arguments: dict[str, Any] | None = None) -> bool:
        result = self.check(tool_name, arguments, interactive=False)
        return result["allowed"] or result["decision"] == "needs_confirmation"

    def _hard_deny(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        if tool_name == "run_shell":
            command = str(arguments.get("command", "")).lower()
            for forbidden in [
                "rm -rf /",
                "mkfs",
                "format c:",
                "dd if=/dev/zero",
                "shutdown -h now",
                "reboot",
                "curl .* | sh",
                "wget .* | sh",
                "chmod -R 777 /",
            ]:
                if forbidden in command:
                    return True
        if tool_name == "capture_screen":
            return bool(arguments.get("target") in {"all", "desktop"})
        if tool_name in {"manage_processes", "set_clipboard"}:
            return bool(arguments.get("force"))
        return False

    def _record(self, tool_name: str, risk_level: RiskLevel, decision: str, reason: str, metadata: dict[str, Any]) -> None:
        self.audit_log.append(
            AuditEntry(
                tool_name=tool_name,
                risk_level=str(risk_level),
                decision=decision,
                reason=reason,
                metadata=metadata,
            )
        )


__all__ = ["SafetyGate", "AuditEntry"]
