import json
import os
import tempfile
import unittest

from babyai.agent.core import BabyAI
from babyai.tools.registry import build_default_registry


class ScriptedLLM:
    """Stands in for LLMClient: returns queued responses and records what it was sent."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, tools=None, stream=False):
        self.calls.append({"messages": messages, "tools": tools})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def say(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def call_tool(name, arguments, call_id="c1"):
    return {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
    ]}}]}


class ToolSchemaTests(unittest.TestCase):
    def setUp(self):
        self.schemas = {t.name: t.schema()["function"]["parameters"] for t in build_default_registry().tools.values()}

    def test_required_and_optional_arguments_come_from_the_signature(self):
        read = self.schemas["read_file"]
        self.assertEqual(read["required"], ["path"])
        self.assertEqual(read["properties"]["path"]["type"], "string")
        self.assertEqual(read["properties"]["offset"]["type"], "integer")  # `int | None` is still an integer
        self.assertEqual(read["properties"]["limit"]["type"], "integer")

    def test_defaults_and_booleans(self):
        shell = self.schemas["run_shell"]
        self.assertEqual(shell["required"], ["command"])
        self.assertEqual(shell["properties"]["timeout"]["default"], 30)
        self.assertEqual(shell["properties"]["shell"]["type"], "boolean")

    def test_tools_without_arguments_have_none(self):
        self.assertEqual(self.schemas["get_system_info"], {"type": "object", "properties": {}, "required": []})

    def test_every_tool_with_a_required_argument_declares_it(self):
        self.assertEqual(self.schemas["write_file"]["required"], ["path", "content"])
        self.assertEqual(self.schemas["remember_fact"]["required"], ["fact"])


class BrainTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        config = os.path.join(self.dir.name, "config.toml")
        with open(config, "w") as handle:
            handle.write(f'[memory]\ndb_path = "{self.dir.name}/mem.db"\n[safety]\nlevel = "strict"\n')
        self.ai = BabyAI(config_path=config)

    def tearDown(self):
        self.dir.cleanup()

    def target(self, name="note.txt"):
        return os.path.join(self.dir.name, name)

    def test_plain_reply_passes_through(self):
        self.ai.llm_client = ScriptedLLM(say("hey"))
        self.assertEqual(self.ai.process_user_input("hi"), "hey")

    def test_low_risk_tool_runs_and_the_model_narrates_the_result(self):
        with open(self.target("a.txt"), "w") as handle:
            handle.write("needle")
        self.ai.llm_client = ScriptedLLM(call_tool("read_file", {"path": self.target("a.txt")}), say("It says needle."))
        self.assertEqual(self.ai.process_user_input("read a.txt"), "It says needle.")
        second = self.ai.llm_client.calls[1]["messages"]
        self.assertEqual(second[-1]["role"], "tool")
        self.assertEqual(second[-1]["tool_call_id"], "c1")
        self.assertIn("needle", second[-1]["content"])
        self.assertEqual(second[-2]["tool_calls"][0]["function"]["name"], "read_file")

    def test_raw_tool_output_is_the_reply_when_the_followup_fails(self):
        with open(self.target("a.txt"), "w") as handle:
            handle.write("needle")
        self.ai.llm_client = ScriptedLLM(call_tool("read_file", {"path": self.target("a.txt")}), RuntimeError("down"))
        reply = self.ai.process_user_input("read a.txt")
        self.assertTrue(reply.startswith("read_file:"))
        self.assertIn("needle", reply)

    def test_confirmation_required_calls_are_declined_without_a_hook(self):
        self.ai.llm_client = ScriptedLLM(call_tool("write_file", {"path": self.target(), "content": "x"}), say("ok"))
        self.ai.process_user_input("write a note")
        self.assertFalse(os.path.exists(self.target()))
        self.assertIn("declined", self.ai.llm_client.calls[1]["messages"][-1]["content"])

    def test_confirmation_hook_can_approve(self):
        asked = []
        self.ai.confirm = lambda name, args, verdict: asked.append((name, verdict["risk_level"])) or True
        self.ai.llm_client = ScriptedLLM(call_tool("write_file", {"path": self.target(), "content": "x"}), say("done"))
        self.assertEqual(self.ai.process_user_input("write a note"), "done")
        self.assertTrue(os.path.exists(self.target()))
        self.assertEqual(asked, [("write_file", "MEDIUM")])
        self.assertEqual(self.ai.safety_gate.audit_log[-1].decision, "approved")

    def test_confirmation_hook_can_decline_and_a_crashing_hook_means_no(self):
        self.ai.confirm = lambda *_: False
        self.ai.llm_client = ScriptedLLM(call_tool("write_file", {"path": self.target(), "content": "x"}), say("fine"))
        self.ai.process_user_input("write a note")
        self.assertFalse(os.path.exists(self.target()))
        self.assertEqual(self.ai.safety_gate.audit_log[-1].decision, "declined")

        def boom(*_):
            raise RuntimeError("ui died")

        self.ai.confirm = boom
        self.ai.llm_client = ScriptedLLM(call_tool("write_file", {"path": self.target(), "content": "x"}), say("fine"))
        self.ai.process_user_input("write a note")
        self.assertFalse(os.path.exists(self.target()))

    def test_dangerous_commands_are_blocked_even_if_the_user_would_say_yes(self):
        self.ai.confirm = lambda *_: True
        self.ai.llm_client = ScriptedLLM(call_tool("run_shell", {"command": f"rm -rf / # {self.dir.name}"}), say("no"))
        self.ai.process_user_input("clean up")
        self.assertIn("Blocked by the safety gate", self.ai.llm_client.calls[1]["messages"][-1]["content"])

    def test_bad_arguments_and_unknown_tools_do_not_crash(self):
        self.ai.llm_client = ScriptedLLM(call_tool("read_file", {"nope": 1}), say("a"))
        self.assertEqual(self.ai.process_user_input("x"), "a")
        self.assertIn("bad arguments", self.ai.llm_client.calls[1]["messages"][-1]["content"])
        self.ai.llm_client = ScriptedLLM(call_tool("teleport", {}), say("b"))
        self.assertEqual(self.ai.process_user_input("y"), "b")
        self.assertIn("no tool called", self.ai.llm_client.calls[1]["messages"][-1]["content"])

    def test_a_tool_that_raises_does_not_crash(self):
        self.ai.llm_client = ScriptedLLM(call_tool("read_file", {"path": self.target("missing.txt")}), say("c"))
        self.assertEqual(self.ai.process_user_input("read it"), "c")

    def test_at_most_four_tool_calls_per_message(self):
        many = {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "get_system_info", "arguments": "{}"}} for i in range(9)
        ]}}]}
        self.ai.llm_client = ScriptedLLM(many, say("done"))
        self.ai.process_user_input("go")
        tool_messages = [m for m in self.ai.llm_client.calls[1]["messages"] if m["role"] == "tool"]
        self.assertEqual(len(tool_messages), 4)


if __name__ == "__main__":
    unittest.main()
