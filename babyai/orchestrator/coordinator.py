"""BabyAI's central coordinator.

The coordinator is a thin front-end-compatible wrapper around the existing
``BabyAI``.  Ordinary conversation is delegated unchanged.  A request that
starts with ``plan:`` (or, if enabled, passes the conservative auto heuristic)
is planned, assigned to specialised workers, verified, and summarised back to
the user.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from babyai.llm.client import LLMClient
from babyai.orchestrator import status
from babyai.orchestrator.models import AgentRole, Task, TaskGraph, TaskStatus
from babyai.orchestrator.planner import Planner
from babyai.orchestrator.task_store import TaskStore
from babyai.orchestrator.verification import Verifier
from babyai.orchestrator.workers import Worker

ProgressHook = Callable[[str], None]

EXPLICIT = re.compile(r"^\s*(?:(?:plan|orchestrate)\s*:|/plan\b)\s*", re.IGNORECASE)
AUTO_WORDS = re.compile(
    r"\b(plan|research|investigate|compare|implement|refactor|review|summarise|summarize|organise|organize)\b",
    re.IGNORECASE,
)


class Coordinator:
    """Presents BabyAI's front-end surface while adding planning and workers."""

    DELEGATED = frozenset({
        "config", "config_path", "portable_root", "voice_enabled", "memory", "safety_gate", "context",
        "upgrade", "habits", "personality", "tool_registry", "ideas", "llm_client", "offline_reply",
        "confirm", "cli_confirm", "start_idea_checks", "_tools_ok",
    })

    def __init__(
        self,
        inner: Any,
        config: dict[str, Any] | None = None,
        planner_llm: Any = None,
        worker_llm: Any = None,
        verifier_llm: Any = None,
        progress: ProgressHook | None = None,
    ):
        object.__setattr__(self, "_inner", inner)
        self._config = config if config is not None else getattr(inner, "config", {})
        self._cfg = self._config.get("orchestrator") or {}
        self.enabled = bool(self._cfg.get("enabled", True))
        self.auto = bool(self._cfg.get("auto", False))
        self.max_tasks = max(1, int(self._cfg.get("max_tasks", 8)))
        self.max_workers = max(1, int(self._cfg.get("max_workers", 1)))
        self.max_tool_calls = max(1, int(self._cfg.get("max_tool_calls", 4)))
        self.max_rounds = max(1, int(self._cfg.get("max_rounds", 3)))
        self.max_retries = max(0, int(self._cfg.get("max_retries", 1)))
        self._planner_override = planner_llm
        self._worker_override = worker_llm
        self._verifier_override = verifier_llm
        self.progress = progress
        self.confirm_lock = threading.Lock()
        self._progress_lock = threading.Lock()

        memory_path = str(self._config.get("memory", {}).get("db_path", "~/.local/share/babyai/memory.db"))
        default_dir = os.path.join(os.path.dirname(os.path.expanduser(memory_path)) or ".", "orchestrator")
        directory = str(self._cfg.get("dir") or default_dir)
        self.task_store = TaskStore(directory) if self.enabled else None
        self.planner = Planner(self._client_for("planner"), max_tasks=self.max_tasks)
        self.verifier = Verifier(
            self._client_for("verifier"),
            use_llm=bool(self._cfg.get("verify_with_llm", False)),
        )

    def __getattr__(self, name: str) -> Any:
        inner = object.__getattribute__(self, "_inner")
        if name in self.DELEGATED:
            return getattr(inner, name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_") or name not in self.DELEGATED:
            object.__setattr__(self, name, value)
            return
        setattr(object.__getattribute__(self, "_inner"), name, value)

    @property
    def inner(self) -> Any:
        return object.__getattribute__(self, "_inner")

    def _client_for(self, kind: str, role: AgentRole | None = None) -> Any:
        overrides = {"planner": self._planner_override, "worker": self._worker_override, "verifier": self._verifier_override}
        override = overrides.get(kind)
        if override is not None:
            return override
        models = self._cfg.get("models") if isinstance(self._cfg.get("models"), dict) else {}
        model = models.get((role.value if role is not None else kind), "")
        model = model or self._cfg.get(f"{kind}_model", "")
        if not model:
            return getattr(self.inner, "llm_client", None)
        llm_cfg = self._config.get("llm", {})
        return LLMClient(
            base_url=llm_cfg.get("base_url", "http://localhost:11434/v1"),
            model=str(model),
            api_key=llm_cfg.get("api_key", ""),
            timeout=llm_cfg.get("timeout", 120),
        )

    def _emit(self, line: str) -> None:
        hook = getattr(self, "progress", None)
        if hook is None:
            return
        with self._progress_lock:
            try:
                hook(line)
            except Exception:
                pass

    def _wanted(self, user_input: str) -> tuple[str, bool]:
        if not self.enabled:
            return user_input, False
        match = EXPLICIT.match(user_input)
        if match:
            goal = user_input[match.end():].strip()
            return (goal or user_input.strip()), True
        if self.auto:
            lowered = user_input.lower()
            complex_request = len(user_input) > 160 or (" and " in lowered and AUTO_WORDS.search(lowered))
            if complex_request:
                return user_input.strip(), True
        return user_input, False

    def run_cli(self) -> None:
        """The REPL lives here so `plan:` works in interactive mode too."""
        inner = self.inner
        if inner.confirm is None and sys.stdin.isatty():
            inner.confirm = inner.cli_confirm
        print("BabyAI starting. Type 'exit' to quit.")
        while True:
            try:
                user_input = input("You> ")
            except EOFError:
                print()
                break
            if user_input.strip().lower() in {"exit", "quit", "bye"}:
                print("BabyAI: Goodbye.")
                break
            print(self.process_user_input(user_input))

    def process_user_input(self, user_input: str) -> str:
        goal, wanted = self._wanted(user_input)
        if not wanted:
            return self.inner.process_user_input(user_input)
        return self._run_plan(goal, user_input)

    def _memory_context(self, goal: str) -> str:
        memory = getattr(self.inner, "memory", None)
        if memory is None:
            return ""
        try:
            facts = memory.recall_memory(goal, limit=4)
        except Exception:
            return ""
        return "\n".join(str(fact.get("fact") or "") for fact in facts if fact.get("fact"))

    def _run_plan(self, goal: str, original: str) -> str:
        plan_id = uuid.uuid4().hex
        self._emit(status.planning(goal))
        try:
            graph = self.planner.plan(goal, self._memory_context(goal))
        except Exception:  # planner/model failure is not allowed to break the assistant
            self._emit(f"{status.PREFIX} planner unavailable; using one worker")
            graph = Planner.simple_plan(goal)
        graph.validate(self.max_tasks)

        store = self.task_store
        if store is not None:
            store.create_plan(plan_id, goal, graph.tasks)
        ordered = self._execute(plan_id, graph)
        if store is not None:
            failed = any(task.status in {TaskStatus.FAILED, TaskStatus.BLOCKED} for task in graph.tasks)
            store.set_plan_status(plan_id, "partial" if failed else "completed")

        reply = self._aggregate(goal, ordered)
        try:
            self.inner.context.add_user_message(original)
            self.inner.context.add_assistant_message(reply)
        except Exception:
            pass
        return reply

    def _execute(self, plan_id: str, graph: TaskGraph) -> list[Task]:
        ordered = graph.order()
        total = len(ordered)
        positions = {task.id: number for number, task in enumerate(ordered, start=1)}
        remaining = list(ordered)
        done: set[str] = set()
        failed: set[str] = set()

        while remaining:
            ready = [task for task in remaining if all(dep in done for dep in task.depends_on)]
            if not ready:
                stuck = [task for task in remaining if any(dep in failed for dep in task.depends_on)]
                if not stuck:
                    stuck = remaining  # a cycle should have been rejected; never spin on one
                for task in stuck:
                    task.status = TaskStatus.BLOCKED
                    task.error = "an earlier task did not complete"
                    self._save(plan_id, task)
                    self._emit(status.blocked(task, positions[task.id], total))
                    failed.add(task.id)
                    remaining.remove(task)
                continue

            results: dict[str, tuple[Task, bool, str]] = {}
            if self.max_workers > 1 and len(ready) > 1:
                with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                    futures = {
                        pool.submit(self._attempt, plan_id, task, positions[task.id], total): task
                        for task in ready
                    }
                    for future, task in futures.items():
                        try:
                            results[task.id] = future.result()
                        except Exception as exc:
                            task.status = TaskStatus.FAILED
                            task.error = str(exc)
                            self._save(plan_id, task)
                            results[task.id] = (task, False, str(exc))
            else:
                for task in ready:
                    results[task.id] = self._attempt(plan_id, task, positions[task.id], total)

            for task in ready:
                completed, ok, _reason = results[task.id]
                (done if ok else failed).add(task.id)
                remaining.remove(task)
                task.status = completed.status
            self._emit(status.finished(len(done), total))
        return ordered

    def _attempt(self, plan_id: str, task: Task, index: int, total: int) -> tuple[Task, bool, str]:
        last_result = None
        reason = "the worker produced no result"
        for attempt in range(self.max_retries + 1):
            task.attempt = attempt + 1
            task.status = TaskStatus.RUNNING
            self._save(plan_id, task)
            self._emit(status.running(task, index, total))
            worker = Worker(
                role=task.role,
                llm_client=self._client_for("worker", task.role),
                tool_registry=getattr(self.inner, "tool_registry", None),
                safety_gate=getattr(self.inner, "safety_gate", None),
                confirm=getattr(self.inner, "confirm", None),
                task_store=self.task_store,
                plan_id=plan_id,
                memory=getattr(self.inner, "memory", None),
                confirm_lock=self.confirm_lock,
                max_tool_calls=self.max_tool_calls,
                max_rounds=self.max_rounds,
                progress=self._emit,
            )
            try:
                last_result = worker.run(task)
                self._emit(status.verifying(task, index, total))
                passed, reason = self.verifier.verify(task, last_result)
            except Exception as exc:
                last_result = None
                passed, reason = False, str(exc)
            if passed:
                task.status = TaskStatus.VERIFIED
                task.result = last_result.content if last_result else ""
                task.error = ""
                self._save(plan_id, task)
                return task, True, ""
            task.result = last_result.content if last_result else ""
            task.error = reason
            self._save(plan_id, task)
            if attempt < self.max_retries:
                self._emit(status.retrying(task, index, total))
        task.status = TaskStatus.FAILED
        self._save(plan_id, task)
        return task, False, reason

    def _save(self, plan_id: str, task: Task) -> None:
        if self.task_store is not None:
            self.task_store.update_task(plan_id, task)

    @staticmethod
    def _aggregate(goal: str, ordered: list[Task]) -> str:
        lines = [f"Plan complete: {goal}"]
        for task in ordered:
            if task.status is TaskStatus.VERIFIED:
                detail = " ".join(task.result.split())
                lines.append(f"- {task.title}: {detail[:600]}")
            elif task.status is TaskStatus.BLOCKED:
                lines.append(f"- {task.title}: skipped because an earlier task did not complete")
            else:
                lines.append(f"- {task.title}: failed ({task.error or 'no result'})")
        if any(task.status is not TaskStatus.VERIFIED for task in ordered):
            lines.append("Some tasks did not complete.")
        return "\n".join(lines)

    def close(self) -> None:
        if self.task_store is not None:
            self.task_store.close()
        close = getattr(self.inner, "close", None)
        if callable(close):
            close()
        else:
            memory = getattr(self.inner, "memory", None)
            if memory is not None:
                memory.close()


__all__ = ["Coordinator"]
