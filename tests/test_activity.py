"""The few words she shows for what she is doing ("SEARCHING THE WEB…") while a tool runs."""

import os
import tempfile
import unittest

from babyai.activity import LABELS, describe_tool
from babyai.personality import Personality
from babyai.tools.registry import build_default_registry
from babyai.ui.behavior import DOING_MAX, thinking_label
from tests.test_brain import ScriptedLLM, call_tool, say
from tests.test_guardrails import AgentBase


class DescribeToolTests(unittest.TestCase):
    def test_every_tool_he_has_gets_a_phrase_of_its_own(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = build_default_registry(config={}, personality=Personality(folder))
            missing = [name for name in registry.tools if name not in LABELS]
        self.assertEqual(missing, [])

    def test_every_phrase_is_for_a_tool_that_exists(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = build_default_registry(config={}, personality=Personality(folder))
            self.assertEqual([name for name in LABELS if name not in registry.tools], [])

    def test_a_phrase_is_short_lowercase_words(self):
        for name, label in LABELS.items():
            self.assertEqual(describe_tool(name), label)
            self.assertRegex(label, r"^[a-z ]{3,32}$", name)

    def test_a_tool_without_a_phrase_is_named_plainly(self):
        self.assertEqual(describe_tool("make_coffee"), "using make coffee")

    def test_a_name_that_is_not_a_tool_name_says_nothing(self):
        for name in ("", "../etc/passwd", "a;rm -rf /", "Read_File", "x" * 80, "two words", None, 7):
            self.assertEqual(describe_tool(name), "", name)


class ThinkingLabelTests(unittest.TestCase):
    def test_what_she_is_doing_is_said_at_once(self):
        self.assertEqual(thinking_label(0, doing="searching the web"), "SEARCHING THE WEB…")
        self.assertEqual(thinking_label(3, doing="searching the web"), "SEARCHING THE WEB…")

    def test_the_count_follows_after_the_usual_wait(self):
        self.assertEqual(thinking_label(15, doing="searching the web"), "SEARCHING THE WEB… 15 s")
        self.assertEqual(thinking_label(4, after_s=4, doing="listening"), "LISTENING… 4 s")

    def test_without_it_nothing_changes(self):
        self.assertIsNone(thinking_label(14.9, doing=""))
        self.assertEqual(thinking_label(15, doing=""), "THINKING… 15 s")

    def test_a_long_or_messy_phrase_is_kept_to_one_short_line(self):
        label = thinking_label(0, doing="  reading\n a   file " + "z" * 100)
        self.assertEqual(label.count("\n"), 0)
        self.assertNotIn("  ", label)
        self.assertEqual(len(label), DOING_MAX + 1)  # (and the dots)


class AgentActivityTests(AgentBase):
    def listen(self):
        told = []
        self.ai.activity = told.append
        return told

    def test_a_tool_is_announced_while_it_runs_and_cleared_when_it_is_done(self):
        told = self.listen()
        seen_while_running = []
        tool = self.ai.tool_registry.get("get_system_info")
        real = tool.invoke
        tool.invoke = lambda *a, **k: (seen_while_running.append(list(told)), real(*a, **k))[1]
        self.ai.llm_client = ScriptedLLM(call_tool("get_system_info", {}), say("Looks fine."))
        self.assertEqual(self.ai.process_user_input("how is the machine?"), "Looks fine.")
        self.assertEqual(seen_while_running, [["checking the system"]])
        self.assertEqual(told, ["checking the system", ""])

    def test_what_the_model_typed_is_never_in_it(self):
        told = self.listen()
        secret = "hunter2-notes.txt"
        self.ai.llm_client = ScriptedLLM(call_tool("read_file", {"path": os.path.join(self.dir.name, secret)}), say("ok"))
        self.ai.process_user_input("read it")
        self.assertEqual(told, ["reading a file", ""])
        self.assertNotIn(secret, " ".join(told))

    def test_a_tool_that_is_refused_is_not_announced(self):
        told = self.listen()
        self.ai.llm_client = ScriptedLLM(call_tool("write_file", {"path": os.path.join(self.dir.name, "x.txt"), "content": "hi"}), say("no"))
        self.ai.process_user_input("write it")  # strict: nobody to ask, so it is declined
        self.assertEqual(told, [])
        self.assertFalse(os.path.exists(os.path.join(self.dir.name, "x.txt")))

    def test_a_tool_that_does_not_exist_is_not_announced(self):
        told = self.listen()
        self.ai.llm_client = ScriptedLLM(call_tool("make_coffee", {}), say("sorry"))
        self.ai.process_user_input("coffee please")
        self.assertEqual(told, [])

    def test_a_tool_that_fails_still_clears_it(self):
        told = self.listen()
        tool = self.ai.tool_registry.get("get_system_info")
        tool.invoke = lambda *a, **k: 1 / 0
        self.ai.llm_client = ScriptedLLM(call_tool("get_system_info", {}), say("It broke."))
        self.assertEqual(self.ai.process_user_input("how is the machine?"), "It broke.")
        self.assertEqual(told, ["checking the system", ""])

    def test_a_broken_display_never_stops_the_tool_or_loses_the_answer(self):
        def broken(text):
            raise RuntimeError("window gone")

        ran = []
        tool = self.ai.tool_registry.get("get_system_info")
        real = tool.invoke
        tool.invoke = lambda *a, **k: (ran.append(True), real(*a, **k))[1]
        self.ai.activity = broken
        self.ai.llm_client = ScriptedLLM(call_tool("get_system_info", {}), say("Looks fine."))
        self.assertEqual(self.ai.process_user_input("how is the machine?"), "Looks fine.")
        self.assertEqual(ran, [True])

    def test_nobody_listening_is_fine(self):
        self.assertIsNone(self.ai.activity)
        self.ai.llm_client = ScriptedLLM(call_tool("get_system_info", {}), say("Looks fine."))
        self.assertEqual(self.ai.process_user_input("how is the machine?"), "Looks fine.")


if __name__ == "__main__":
    unittest.main()
