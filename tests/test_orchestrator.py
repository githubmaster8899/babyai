"""Tests for BabyAI's planning/task-graph orchestration.

No network: the planner, workers and verifier all get scripted LLM stand-ins.
These tests pin the important boundary - orchestration reuses the ordinary
safety gate and grants no new tools or permissions.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from babyai import portable
from babyai.agent.core import BabyAI
from babyai.config.loader import DEFAULT_CONFIG
from babyai.orchestrator import AgentRole, Coordinator, PlanError, Task, TaskGraph, TaskStatus, TaskStore
from babyai.orchestrator.planner import Planner
from babyai.tools.registry import build_default_registry
from tests.test_brain import ScriptedLLM, call_tool, say


def plan_response(tasks, goal="do the thing"):
    return say(json.dumps({"goal": goal, "tasks": tasks}))


def task(task_id, goal, role="general", depends_on=(), expected=""):
    return {
        "id": task_id,
        "title": task_id,
        "role": role,
        "goal": goal,
        "depends_on": list(depends_on),
        "expected_artifact": expected,
    }


class OrchestratorBase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.config_path = self.root / "config.toml"
        self.coordinator = None

    def tearDown(self):
        if self.coordinator is not None:
            self.coordinator.close()
        self.directory.cleanup()

    def make(self, planner=None, worker=None, verifier=None, progress=None, extra=""):
        retries = "" if "max_retries" in extra else "max_retries = 0\n"
        verify = "" if "verify_with_llm" in extra else "verify_with_llm = false\n"
        self.config_path.write_text(
            f'[memory]\ndb_path = "{self.root}/memory.db"\n'
            f'[upgrade]\ndir = "{self.root}/self"\n'
            '[safety]\nlevel = "strict"\n'
            '[orchestrator]\n'
            f'dir = "{self.root}/tasks"\n'
            f'max_workers = 1\n{retries}max_tool_calls = 4\nmax_rounds = 3\n{verify}'
            f'{extra}'
        )
        inner = BabyAI(config_path=str(self.config_path))
        coordinator = Coordinator(
            inner,
            planner_llm=planner if planner is not None else ScriptedLLM(),
            worker_llm=worker if worker is not None else ScriptedLLM(),
            verifier_llm=verifier,
            progress=progress,
        )
        self.coordinator = coordinator
        return inner, coordinator


class PlanValidationTests(unittest.TestCase):
    def test_task_order_follows_dependencies_not_planner_order(self):
        graph = TaskGraph("goal", [
            Task(id="t3", title="third", goal="c", depends_on=("t2",)),
            Task(id="t1", title="first", goal="a"),
            Task(id="t2", title="second", goal="b", depends_on=("t1",)),
        ]).validate()
        self.assertEqual([task.id for task in graph.order()], ["t1", "t2", "t3"])

    def test_cycles_duplicate_ids_and_unknown_dependencies_are_rejected(self):
        bad_graphs = (
            [Task(id="a", goal="a", depends_on=("b",)), Task(id="b", goal="b", depends_on=("a",))],
            [Task(id="a", goal="a"), Task(id="a", goal="again")],
            [Task(id="a", goal="a", depends_on=("missing",))],
            [Task(id="a", goal="")],
        )
        for tasks in bad_graphs:
            with self.subTest(tasks=[task.id for task in tasks]):
                with self.assertRaises(PlanError):
                    TaskGraph("goal", tasks).validate()

    def test_the_planner_extracts_json_from_a_fenced_reply(self):
        payload = {"tasks": [task("t1", "find facts", role="research", expected="a summary")]}
        planner = Planner(ScriptedLLM(say("```json\n" + json.dumps(payload) + "\n```")))
        graph = planner.plan("find out what happened")
        self.assertEqual([task.id for task in graph.tasks], ["t1"])
        self.assertEqual(graph.tasks[0].role, AgentRole.RESEARCH)
        self.assertEqual(graph.tasks[0].expected_artifact, "a summary")

    def test_unknown_roles_become_general(self):
        planner = Planner(ScriptedLLM())
        graph = planner.from_dict("goal", {"tasks": [task("t1", "do it", role="delete_everything")]})
        self.assertEqual(graph.tasks[0].role, AgentRole.GENERAL)


class CoordinatorTests(OrchestratorBase):
    def test_dependencies_wait_for_earlier_artifacts(self):
        planner = ScriptedLLM(plan_response([
            task("t2", "write the second draft", depends_on=["t1"]),
            task("t1", "find the facts", role="research", expected="the facts"),
        ]))
        worker = ScriptedLLM(say("FIRST RESULT"), say("SECOND RESULT"))
        progress = []
        _inner, coordinator = self.make(planner, worker, progress=progress.append)

        reply = coordinator.process_user_input("plan: research then draft")
        self.assertIn("Plan complete:", reply)
        self.assertIn("FIRST RESULT", reply)
        self.assertIn("SECOND RESULT", reply)
        self.assertEqual(len(worker.calls), 2)
        self.assertIn("Artifact from t1", worker.calls[1]["messages"][-1]["content"])
        self.assertIn("FIRST RESULT", worker.calls[1]["messages"][-1]["content"])
        running = [line for line in progress if "/2 " in line]
        self.assertTrue(any("t1" in line for line in running), running)
        self.assertTrue(any("t2" in line for line in running), running)

    def test_worker_tool_calls_go_through_the_safety_gate(self):
        note = self.root / "note.txt"
        note.write_text("needle")
        planner = ScriptedLLM(plan_response([task("t1", "read the note")]))
        worker = ScriptedLLM(call_tool("read_file", {"path": str(note)}), say("It says needle."))
        inner, coordinator = self.make(planner, worker)

        reply = coordinator.process_user_input("plan: read the note")
        self.assertIn("It says needle.", reply)
        self.assertTrue(any(entry.tool_name == "read_file" for entry in inner.safety_gate.audit_log))

    def test_confirmation_is_fail_closed(self):
        target = self.root / "declined.txt"
        planner = ScriptedLLM(plan_response([task("t1", "write a file", role="coding")]))
        worker = ScriptedLLM(call_tool("write_file", {"path": str(target), "content": "x"}), say("done"))
        inner, coordinator = self.make(planner, worker)
        self.assertIsNone(coordinator.confirm)

        reply = coordinator.process_user_input("plan: write a file")
        self.assertFalse(target.exists())
        self.assertIn("failed", reply)
        self.assertTrue(any(entry.decision == "declined" for entry in inner.safety_gate.audit_log))

    def test_confirmation_can_be_approved(self):
        target = self.root / "approved.txt"
        planner = ScriptedLLM(plan_response([task("t1", "write a file", role="coding")]))
        worker = ScriptedLLM(call_tool("write_file", {"path": str(target), "content": "x"}), say("done"))
        inner, coordinator = self.make(planner, worker)
        asked = []
        coordinator.confirm = lambda name, args, verdict: asked.append(name) or True

        reply = coordinator.process_user_input("plan: write a file")
        self.assertTrue(target.exists())
        self.assertTrue(asked)
        self.assertTrue(any(entry.decision == "approved" for entry in inner.safety_gate.audit_log))
        self.assertIn("Plan complete:", reply)
        self.assertNotIn("Some tasks did not complete", reply)

    def test_a_rejected_result_is_retried_and_then_verified(self):
        planner = ScriptedLLM(plan_response([
            task("t1", "answer carefully", expected="a complete answer"),
        ]))
        worker = ScriptedLLM(say("vague"), say("a complete answer with detail"))
        verifier = ScriptedLLM(say("NO: too vague"), say("YES: this covers it"))
        _inner, coordinator = self.make(
            planner, worker, verifier, extra="verify_with_llm = true\nmax_retries = 1\n"
        )

        reply = coordinator.process_user_input("plan: answer carefully")
        self.assertEqual(len(worker.calls), 2)
        self.assertEqual(len(verifier.calls), 2)
        self.assertIn("a complete answer with detail", reply)
        self.assertNotIn("Some tasks did not complete", reply)

    def test_failed_planner_falls_back_to_one_general_worker(self):
        planner = ScriptedLLM(say("the model wrote prose instead of a plan"))
        worker = ScriptedLLM(say("a direct answer"))
        progress = []
        _inner, coordinator = self.make(planner, worker, progress=progress.append)

        reply = coordinator.process_user_input("plan: just answer this")
        self.assertIn("a direct answer", reply)
        self.assertTrue(any("planner unavailable" in line for line in progress))

    def test_ordinary_chat_is_delegated_without_planning(self):
        planner = ScriptedLLM(say("should not be used"))
        inner, coordinator = self.make(planner)
        inner.llm_client = ScriptedLLM(say("hello from the ordinary path"))

        self.assertEqual(coordinator.process_user_input("hi"), "hello from the ordinary path")
        self.assertEqual(planner.calls, [])
        self.assertEqual(len(inner.llm_client.calls), 1)

    def test_interactive_cli_uses_the_coordinator_too(self):
        planner = ScriptedLLM(plan_response([task("t1", "do it")]))
        worker = ScriptedLLM(say("cli worker answer"))
        _inner, coordinator = self.make(planner, worker)
        out = StringIO()
        with mock.patch("builtins.input", side_effect=["plan: do it", "exit"]), redirect_stdout(out):
            coordinator.run_cli()
        self.assertIn("cli worker answer", out.getvalue())
        self.assertEqual(len(planner.calls), 1)

    def test_auto_planning_is_off_until_asked_for(self):
        planner = ScriptedLLM(plan_response([task("t1", "do it")]))
        inner, coordinator = self.make(planner)
        inner.llm_client = ScriptedLLM(say("plain reply"))

        self.assertEqual(coordinator.process_user_input("compare these long notes " + "word " * 50), "plain reply")
        self.assertEqual(planner.calls, [])

    def test_it_adds_no_model_visible_tools(self):
        registry = build_default_registry()
        self.assertEqual(len(registry.tools), 23)
        for name in ("create_plan", "delegate_task", "orchestrate", "task_status"):
            self.assertNotIn(name, registry.tools)


class StorageAndPortabilityTests(unittest.TestCase):
    def test_task_store_survives_reopening(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(directory)
            tasks = [Task(id="t1", goal="find facts", role=AgentRole.RESEARCH)]
            self.assertTrue(store.create_plan("plan-1", "the goal", tasks))
            tasks[0].status = TaskStatus.VERIFIED
            tasks[0].result = "the facts"
            store.update_task("plan-1", tasks[0])
            self.assertTrue(store.record_artifact("plan-1", "t1", "the facts", source="research"))

            reopened = TaskStore(directory)
            saved = reopened.get_tasks("plan-1")
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].status, TaskStatus.VERIFIED)
            self.assertEqual(saved[0].result, "the facts")
            self.assertEqual(reopened.artifacts_for("plan-1", "t1"), ["the facts"])
            self.assertEqual(reopened.get_plan("plan-1")["goal"], "the goal")
            store.close()
            reopened.close()

    def test_portable_keeps_orchestrator_data_on_the_stick(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {"memory": {}, "upgrade": {}, "habits": {}, "personality": {}}
            portable.apply_paths(config, root)
            portable.prepare_environment(root)
            expected = str(root / "data" / "orchestrator")
            self.assertEqual(config["orchestrator"]["dir"], expected)
            self.assertTrue(Path(expected).is_dir())
            self.assertNotIn(os.path.expanduser("~"), expected)

    def test_config_has_safe_orchestrator_defaults(self):
        self.assertFalse(DEFAULT_CONFIG["orchestrator"]["auto"])
        self.assertTrue(DEFAULT_CONFIG["orchestrator"]["enabled"])
        self.assertEqual(DEFAULT_CONFIG["orchestrator"]["max_workers"], 1)
        self.assertFalse(DEFAULT_CONFIG["orchestrator"]["verify_with_llm"])


if __name__ == "__main__":
    unittest.main()
