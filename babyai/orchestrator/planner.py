"""The planning agent: turn a goal into a small, validated task graph.

The model proposes tasks; Python decides what is actually runnable.  A bad plan
(a cycle, a missing dependency, a duplicate id, too many tasks) is rejected
rather than executed.  If the model cannot produce a plan, the coordinator can
fall back to one general worker.
"""

from __future__ import annotations

import json
from typing import Any

from babyai.orchestrator.models import AgentRole, PlanError, Task, TaskGraph


PLANNER_SYSTEM = """You are the planning agent for BabyAI, a local-first assistant.
Turn the user's goal into a short directed acyclic graph of tasks for specialised workers.
Reply with ONE JSON object and no other text. Shape:
{"goal": "...", "tasks": [
  {"id": "t1", "title": "short label", "role": "research|coding|memory|verification|general",
   "goal": "what this worker should do", "depends_on": [], "expected_artifact": "what a good result contains"}
]}
Rules: keep it small (at most {max_tasks} tasks); use dependencies only when a task really needs an earlier result;
never reference an id that is not in the plan; never plan a cycle. Plan safe, local, user-approved work only.
Do not plan penetration testing, exploitation, unauthorized access, or attack automation. Do not run tools yourself."""

ROLE_NAMES = ", ".join(role.value for role in AgentRole)


def _extract_json(text: Any) -> dict[str, Any] | None:
    """Find the first complete JSON object in a model reply (fences and prose are fine)."""
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        return None
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


class Planner:
    def __init__(self, llm_client: Any, max_tasks: int = 8):
        self.llm_client = llm_client
        self.max_tasks = max_tasks

    def plan(self, goal: str, context: str = "") -> TaskGraph:
        system = PLANNER_SYSTEM.replace("{max_tasks}", str(self.max_tasks)).replace(
            "research|coding|memory|verification|general", ROLE_NAMES
        )
        user = f"Goal: {goal}"
        if context:
            user += f"\nRelevant things BabyAI already knows:\n{context}"
        response = self.llm_client.complete(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            tools=None,
            stream=False,
        )
        message = response.get("choices", [{}])[0].get("message", {}) if response else {}
        raw = _extract_json(message.get("content") or "")
        if raw is None:
            raise PlanError("the planner did not return a JSON plan")
        return self.from_dict(goal, raw)

    def from_dict(self, goal: str, raw: dict[str, Any]) -> TaskGraph:
        records = raw.get("tasks")
        if isinstance(records, dict):
            records = [dict(value, id=key) for key, value in records.items() if isinstance(value, dict)]
        if not isinstance(records, list) or not records:
            raise PlanError("the plan has no tasks list")
        tasks: list[Task] = []
        for number, record in enumerate(records, start=1):
            if not isinstance(record, dict):
                raise PlanError("a task in the plan is not an object")
            identifier = str(record.get("id") or record.get("task_id") or f"t{number}").strip()
            description = record.get("goal") or record.get("description") or record.get("title") or ""
            role = str(record.get("role") or record.get("agent") or record.get("worker") or "general").lower()
            try:
                role_enum = AgentRole(role)
            except ValueError:
                role_enum = AgentRole.GENERAL
            dependencies = _as_list(record.get("depends_on") or record.get("dependencies") or record.get("after"))
            tasks.append(Task(
                id=identifier,
                title=str(record.get("title") or identifier),
                goal=str(description),
                role=role_enum,
                depends_on=tuple(str(item) for item in dependencies if str(item).strip()),
                expected_artifact=str(record.get("expected_artifact") or record.get("expected") or record.get("output") or ""),
            ))
        return TaskGraph(goal=str(raw.get("goal") or goal), tasks=tasks).validate(self.max_tasks)

    @staticmethod
    def simple_plan(goal: str) -> TaskGraph:
        """One safe task for when no structured plan is available."""
        return TaskGraph(goal=goal, tasks=[Task(
            id="t1",
            title="Handle request",
            goal=goal,
            role=AgentRole.GENERAL,
            expected_artifact="A direct, useful answer to the user's request.",
        )])


__all__ = ["Planner", "PLANNER_SYSTEM", "ROLE_NAMES"]
