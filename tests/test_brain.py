import json
import os
import tempfile
import unittest

from babyai.agent.core import BabyAI, text_tool_call
from babyai.tools.registry import build_default_registry


class ScriptedLLM:
    """Stands in for LLMClient: returns queued responses and records what it was sent."""

    base_url = "http://localhost:11434/v1"

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


class TextToolCallTests(unittest.TestCase):
    """A model that writes its tool call out as text instead of using tool_calls (llama3.1:8b does this)."""

    setUp, tearDown, target = BrainTests.setUp, BrainTests.tearDown, BrainTests.target

    def written(self, text, then="Yep, I can code."):
        return ScriptedLLM(say(text), say(then))

    def test_a_call_written_as_text_runs_the_tool_and_the_model_narrates(self):
        self.ai.llm_client = self.written('{"name":"ask_user","parameters":{"question":"Can you code?"}}')
        self.assertEqual(self.ai.process_user_input("Can you code?"), "Yep, I can code.")
        second = self.ai.llm_client.calls[1]["messages"]
        self.assertEqual(second[-1]["role"], "tool")
        self.assertIn("Can you code?", second[-1]["content"])
        self.assertEqual(second[-2]["tool_calls"][0]["function"]["name"], "ask_user")
        self.assertEqual(second[-2]["content"], "")  # the JSON is not kept as something she said
        self.assertNotIn("parameters", str(self.ai.context.history))

    def test_a_slightly_broken_call_is_still_understood(self):
        self.ai.llm_client = self.written('{"name":"ask_user",""parameters":{"question":"Can you code?"}}')
        self.assertEqual(self.ai.process_user_input("Can you code?"), "Yep, I can code.")
        self.assertIn("Can you code?", self.ai.llm_client.calls[1]["messages"][-1]["content"])

    def test_a_code_fenced_call_with_arguments_is_understood(self):
        with open(self.target("a.txt"), "w") as handle:
            handle.write("needle")
        text = '```json\n{"name": "read_file", "arguments": {"path": %s}}\n```' % json.dumps(self.target("a.txt"))
        self.ai.llm_client = self.written(text, "It says needle.")
        self.assertEqual(self.ai.process_user_input("read a.txt"), "It says needle.")
        self.assertIn("needle", self.ai.llm_client.calls[1]["messages"][-1]["content"])

    def test_a_call_written_as_text_still_goes_through_the_safety_gate(self):
        text = json.dumps({"name": "write_file", "parameters": {"path": self.target(), "content": "x"}})
        self.ai.llm_client = self.written(text, "Okay, I left it alone.")
        self.ai.process_user_input("write a note")
        self.assertFalse(os.path.exists(self.target()))  # strict level, nobody to ask: declined
        self.assertIn("declined", self.ai.llm_client.calls[1]["messages"][-1]["content"])

    def test_a_tool_that_does_not_exist_is_reported_to_the_model_not_shown(self):
        self.ai.llm_client = self.written('{"name":"fly_to_the_moon","parameters":{}}', "I can't do that one.")
        self.assertEqual(self.ai.process_user_input("fly"), "I can't do that one.")
        self.assertIn("no tool called", self.ai.llm_client.calls[1]["messages"][-1]["content"])

    def test_if_the_model_keeps_writing_calls_she_shows_what_the_tool_said_not_json(self):
        self.ai.llm_client = ScriptedLLM(
            say('{"name":"ask_user","parameters":{"question":"Can you code?"}}'),
            say('{"name":"ask_user","parameters":{"question":"Again?"}}'),
        )
        reply = self.ai.process_user_input("Can you code?")
        self.assertTrue(reply.startswith("ask_user:"))
        self.assertNotIn("parameters", reply)

    def test_prose_that_merely_contains_json_is_left_alone(self):
        text = 'Sure! Here is {"name": "x", "parameters": {}} as asked.'
        self.ai.llm_client = ScriptedLLM(say(text))
        self.assertEqual(self.ai.process_user_input("show me json"), text)
        self.assertEqual(len(self.ai.llm_client.calls), 1)

    def test_nothing_is_parsed_when_the_model_cannot_use_tools(self):
        self.ai._tools_ok = False
        text = '{"name":"ask_user","parameters":{"question":"hi"}}'
        self.ai.llm_client = ScriptedLLM(say(text))
        self.assertEqual(self.ai.process_user_input("hi"), text)


class TextToolCallParserTests(unittest.TestCase):
    def test_shapes_it_accepts(self):
        self.assertEqual(text_tool_call('{"name":"a","parameters":{"x":1}}'), ("a", {"x": 1}))
        self.assertEqual(text_tool_call('  {"name": "a", "arguments": {}}\n'), ("a", {}))
        self.assertEqual(text_tool_call('```\n{"name":"a","args":{"x":"}"}}\n```'), ("a", {"x": "}"}))
        self.assertEqual(text_tool_call('{"name":"a",""parameters":{"x":1}}'), ("a", {"x": 1}))
        self.assertEqual(text_tool_call('{"name":"a","parameters":{"x":1}'), ("a", {"x": 1}))  # a missing last brace

    def test_shapes_it_refuses(self):
        for text in ("hello", "", None, 5, '{"name":"a"}', '{"name":"a","parameters":{}} and more words',
                     'Sure {"name":"a","parameters":{}}', '{"name":"a","parameters":{"x":1'):
            self.assertIsNone(text_tool_call(text), text)


class OfflineAndMemoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        config = os.path.join(self.dir.name, "config.toml")
        with open(config, "w") as handle:
            handle.write(f'[memory]\ndb_path = "{self.dir.name}/mem.db"\n')
        self.ai = BabyAI(config_path=config)

    def tearDown(self):
        self.dir.cleanup()

    def test_plain_cli_keeps_its_canned_text_mode_reply(self):
        self.ai.llm_client = ScriptedLLM(RuntimeError("LLM request failed: <urlopen error [Errno 111] Connection refused>"))
        self.assertIn("BabyAI", self.ai.process_user_input("hello"))

    def test_the_character_says_what_is_wrong_when_the_model_is_unreachable(self):
        from babyai.ui.persona import apply_character

        apply_character(self.ai)
        self.ai.llm_client = ScriptedLLM(
            RuntimeError("LLM request failed: <urlopen error [Errno 111] Connection refused>"), say("back online"),
        )
        reply = self.ai.process_user_input("hello")
        self.assertIn("localhost:11434", reply)
        self.assertIn("Connection refused", reply)
        self.assertNotIn("urlopen", reply)
        self.assertNotIn("BabyAI", reply)
        self.assertEqual(self.ai.context.history, [])  # the failed turn is not remembered
        self.assertEqual(self.ai.process_user_input("hello again"), "back online")
        self.assertEqual([m["role"] for m in self.ai.context.history], ["user", "assistant"])

    def test_a_reply_template_someone_set_is_not_overwritten(self):
        from babyai.ui.persona import apply_character

        self.ai.offline_reply = "custom {url}"
        apply_character(self.ai)
        self.assertEqual(self.ai.offline_reply, "custom {url}")

    def test_the_stick_figure_keeps_the_plain_replies(self):
        from babyai.ui.persona import apply_character

        self.ai.config["ui"]["character"] = "stick"
        apply_character(self.ai)
        self.assertIsNone(self.ai.offline_reply)

    def test_memory_tools_work_through_the_brain(self):
        self.ai.llm_client = ScriptedLLM(call_tool("remember_fact", {"fact": "The user loves pineapple pizza."}), say("Noted."))
        self.assertEqual(self.ai.process_user_input("remember I love pineapple pizza"), "Noted.")  # LOW risk: no prompt
        self.assertTrue(self.ai.memory.search_memory("pineapple"))

        self.ai.llm_client = ScriptedLLM(call_tool("recall_memory", {"query": "pineapple"}), say("You love pineapple pizza."))
        self.assertEqual(self.ai.process_user_input("what do I like?"), "You love pineapple pizza.")
        self.assertIn("pineapple", self.ai.llm_client.calls[1]["messages"][-1]["content"])  # the model was shown the memory

    def test_memory_tools_work_when_the_overlay_asks_from_a_worker_thread(self):
        import threading

        replies = []

        def turn():
            self.ai.llm_client = ScriptedLLM(call_tool("remember_fact", {"fact": "The user has a cat called Miso."}), say("Noted."))
            replies.append(self.ai.process_user_input("remember my cat is Miso"))
            self.ai.llm_client = ScriptedLLM(call_tool("recall_memory", {"query": "what's my cat's name?"}), say("Miso."))
            replies.append(self.ai.process_user_input("what's my cat's name?"))

        worker = threading.Thread(target=turn)
        worker.start()
        worker.join()
        self.assertEqual(replies, ["Noted.", "Miso."])
        shown_to_the_model = self.ai.llm_client.calls[1]["messages"][-1]["content"]
        self.assertIn("Miso", shown_to_the_model)  # the tool worked rather than reporting an error
        self.assertNotIn("failed", shown_to_the_model)

    def test_forgetting_asks_first(self):
        self.ai.memory.remember_fact("The user loves pineapple pizza.", "user")
        self.ai.llm_client = ScriptedLLM(call_tool("forget_memory", {"query": "pineapple"}), say("ok"))
        self.ai.process_user_input("forget the pizza thing")  # no confirm hook -> declined
        self.assertTrue(self.ai.memory.search_memory("pineapple"))
        self.ai.confirm = lambda *_: True
        self.ai.llm_client = ScriptedLLM(call_tool("forget_memory", {"query": "pineapple"}), say("gone"))
        self.ai.process_user_input("yes forget it")
        self.assertFalse(self.ai.memory.search_memory("pineapple"))


if __name__ == "__main__":
    unittest.main()
