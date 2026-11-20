"""Display-free progress lines for orchestration.

The coordinator calls a plain ``(text) -> None`` hook.  Front ends decide where
to show it; these functions only make short, stable strings with no control
characters and no model-generated prose.
"""

from __future__ import annotations

from babyai.orchestrator.models import AgentRole, Task

PREFIX = "[orchestrator]"


def _clean(text: str, limit: int = 70) -> str:
    return " ".join(str(text).split())[:limit]


def planning(goal: str = "") -> str:
    return f"{PREFIX} planning{': ' + _clean(goal) if goal else ''}"


def running(task: Task, index: int, total: int) -> str:
    role = task.role.value if isinstance(task.role, AgentRole) else str(task.role)
    return f"{PREFIX} {index}/{total} {role}: {_clean(task.title)}"


def verifying(task: Task, index: int, total: int) -> str:
    return f"{PREFIX} {index}/{total} checking: {_clean(task.title)}"


def retrying(task: Task, index: int, total: int) -> str:
    return f"{PREFIX} {index}/{total} retrying: {_clean(task.title)}"


def blocked(task: Task, index: int, total: int) -> str:
    return f"{PREFIX} {index}/{total} skipped: {_clean(task.title)}"


def finished(done: int, total: int) -> str:
    return f"{PREFIX} done {done}/{total}"


__all__ = ["planning", "running", "verifying", "retrying", "blocked", "finished", "PREFIX"]
