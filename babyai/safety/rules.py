from __future__ import annotations

import json
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
        self.level = level
        self.audit_log: list[AuditEntry] = []

    def check(self, tool_name: str, arguments: dict[str, Any] | None = None, interactive: bool = True) -> dict[str, Any]:
        args = arguments or {}
        risk_level = get_risk_level(tool_name, args)
        reason = ""
        denied = False

        if tool_name == "run_shell":
            command = str(args.get("command", ""))
            if is_dangerous_command(command):
                denied = True
                reason = "Dangerous shell command pattern detected"
                decision = "deny"
                self._record(tool_name, risk_level, decision, reason, metadata={"command": command})
                return {"allowed": False, "decision": decision, "needs_confirmation": False, "risk_level": risk_level, "reason": reason}

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

        if self.level == "permissive" and risk_level in {RiskLevel.MEDIUM, RiskLevel.HIGH}:
            needs_confirmation = False

        if not self._hard_deny(tool_name, args):
            denied = True
            reason = "Hard deny rule matched"
            decision = "deny"
            self._record(tool_name, risk_level, decision, reason, metadata=args)
            return {"allowed": False, "decision": decision, "needs_confirmation": False, "risk_level": risk_level, "reason": reason}

        if needs_confirmation:
            decision = "needs_confirmation"
            self._record(tool_name, risk_level, decision, reason, metadata=args)
            return {"allowed": False, "decision": decision, "needs_confirmation": True, "risk_level": risk_level, "reason": reason}

        decision = "allow"
        self._record(tool_name, risk_level, decision, reason, metadata=args)
        return {"allowed": True, "decision": decision, "needs_confirmation": False, "risk_level": risk_level, "reason": reason}

    def approve(self, tool_name: str, arguments: dict[str, Any] | None = None) -> bool:
        result = self.check(tool_name, arguments, interactive=False)
        if result["decision"] == "deny":
            return False
        if result["decision"] == "needs_confirmation":
            return True
        return True

    def _hard_deny(self, tool_name: str, args: dict[str, Any]) -> bool:
        if tool_name == "run_shell":
            command = str(args.get("command", ""))
            deny_patterns = [
                "rm -rf /",
                "mkfs",
                "format c:",
                "dd if=/dev/zero",
                "shutdown -h now",
                "reboot",
                "curl .* | sh",
                "wget .* | sh",
            ]
            lowered = command.lower()
            for pattern in deny_patterns:
                if pattern.lower() in lowered:
                    return False
        if tool_name == "capture_screen":
            return False if args.get("target") in {"all", "desktop"} else True
        if tool_name in {"set_clipboard", "manage_processes"}:
            if args.get("force"):
                return False
        return True

    def _record(self, tool_name: str, risk_level: str, decision: str, reason: str, metadata: dict[str, Any]) -> None:
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
