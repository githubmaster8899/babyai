"""Data shapes for BabyAI's planning and task graph.

Everything here is plain JSON-compatible data.  Plans may be persisted, passed
between workers, or shown to the user, so no live objects or model internals are
kept in these dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PlanError(ValueError):
    """A plan was malformed, unsafe to run, or could not be ordered."""


class AgentRole(str, Enum):
    GENERAL = "general"
    RESEARCH = "research"
    CODING = "coding"
    MEMORY = "memory"
    VERIFICATION = "verification"


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    VERIFIED = "verified"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class Task:
    id: str
    goal: str
    title: str = ""
    role: AgentRole = AgentRole.GENERAL
    depends_on: tuple[str, ...] = ()
    expected_artifact: str = ""
    status: TaskStatus = TaskStatus.PENDING
    attempt: int = 0
    result: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "goal": self.goal,
            "role": self.role.value,
            "depends_on": list(self.depends_on),
            "expected_artifact": self.expected_artifact,
            "status": self.status.value,
            "attempt": self.attempt,
            "result": self.result,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Task":
        role = raw.get("role", AgentRole.GENERAL.value)
        status = raw.get("status", TaskStatus.PENDING.value)
        try:
            role_enum = AgentRole(str(role).lower())
        except ValueError:
            role_enum = AgentRole.GENERAL
        try:
            status_enum = TaskStatus(str(status).lower())
        except ValueError:
            status_enum = TaskStatus.PENDING
        dependencies = raw.get("depends_on") or ()
        if isinstance(dependencies, str):
            dependencies = (dependencies,)
        return cls(
            id=str(raw.get("id") or ""),
            title=str(raw.get("title") or raw.get("id") or "task"),
            goal=str(raw.get("goal") or ""),
            role=role_enum,
            depends_on=tuple(str(item) for item in dependencies),
            expected_artifact=str(raw.get("expected_artifact") or ""),
            status=status_enum,
            attempt=int(raw.get("attempt") or 0),
            result=str(raw.get("result") or ""),
            error=str(raw.get("error") or ""),
        )


@dataclass
class TaskGraph:
    goal: str
    tasks: list[Task] = field(default_factory=list)

    def validate(self, max_tasks: int = 8) -> "TaskGraph":
        if not self.tasks:
            raise PlanError("the plan has no tasks")
        if len(self.tasks) > max_tasks:
            raise PlanError(f"the plan has {len(self.tasks)} tasks; the limit is {max_tasks}")
        seen: set[str] = set()
        for task in self.tasks:
            if not task.id or not task.goal:
                raise PlanError("every task needs an id and a goal")
            if task.id in seen:
                raise PlanError(f"duplicate task id {task.id!r}")
            seen.add(task.id)
            for dependency in task.depends_on:
                if dependency == task.id:
                    raise PlanError(f"task {task.id!r} depends on itself")
                if dependency not in {other.id for other in self.tasks}:
                    raise PlanError(f"task {task.id!r} depends on unknown task {dependency!r}")
        self.order()  # raises on a cycle
        return self

    def order(self) -> list[Task]:
        """Topologically order the graph, keeping the planner's order where possible."""
        remaining = {task.id: task for task in self.tasks}
        done: set[str] = set()
        ordered: list[Task] = []
        while remaining:
            ready = [
                task for task in remaining.values()
                if all(dep in done for dep in task.depends_on)
            ]
            if not ready:
                raise PlanError("the plan has a dependency cycle")
            for task in ready:
                ordered.append(task)
                done.add(task.id)
                remaining.pop(task.id, None)
        return ordered

    def get(self, task_id: str) -> Task | None:
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"goal": self.goal, "tasks": [task.to_dict() for task in self.tasks]}


@dataclass
class TaskResult:
    task_id: str
    role: AgentRole
    content: str = ""
    artifacts: list[str] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.content.strip()) and not self.error
