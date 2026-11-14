import json
import os
import tempfile
import unittest

from babyai.agent.core import BabyAI
from babyai.memory.store import MemoryStore
from babyai.personality import Personality, clamp, defaults_from_config
from babyai.safety.gate import SafetyGate
from babyai.tools.personality import PersonalityTool
from babyai.tools.registry import build_default_registry


class PersonalityFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def test_starting_values_come_from_config(self):
        personality = Personality(self.dir.name, {"warmth": 5, "verbosity": 1})
        self.assertEqual(personality.values()["warmth"], 5)
        self.assertEqual(personality.values()["verbosity"], 1)
        self.assertEqual(personality.values()["playfulness"], 3)

    def test_a_moved_slider_is_still_moved_next_time(self):
        Personality(self.dir.name).set("curiosity", 5)
        again = Personality(self.dir.name)
        self.assertEqual(again.values()["curiosity"], 5)

    def test_reset_goes_back_to_the_configured_starting_values(self):
        personality = Personality(self.dir.name, {"warmth": 4})
        personality.set("warmth", 1)
        personality.reset()
        self.assertEqual(personality.values()["warmth"], 4)

    def test_show_names_every_slider_and_the_file(self):
        text = Personality(self.dir.name).show()
        for name in ("playfulness", "warmth", "curiosity", "verbosity"):
            self.assertIn(name, text)
        self.assertIn("personality.json", text)

    def test_prompt_block_adds_nothing_until_a_slider_moves(self):
        personality = Personality(self.dir.name)
        self.assertEqual(personality.prompt_block(), "")
        personality.set("warmth", 5)
        block = personality.prompt_block()
        self.assertIn("- warmth:", block)
        self.assertNotIn("- playfulness:", block)

    def test_unknown_slider_is_refused(self):
        with self.assertRaises(ValueError):
            Personality(self.dir.name).set("loudness", 3)

    def test_value_stays_inside_the_slider_range(self):
        personality = Personality(self.dir.name)
        with self.assertRaises(ValueError):
            personality.set("playfulness", 99)
        self.assertEqual(personality.values()["playfulness"], 3)
        self.assertEqual(clamp("loud", default=2), 2)
        self.assertEqual(clamp(True, default=2), 2)

    def test_a_damaged_file_falls_back_to_the_defaults(self):
        with open(os.path.join(self.dir.name, "personality.json"), "w") as handle:
            handle.write("{not json")
        self.assertEqual(Personality(self.dir.name).values()["warmth"], 3)

    def test_config_section_defaults(self):
        self.assertEqual(defaults_from_config({"warmth": 5})["warmth"], 5)
        self.assertEqual(defaults_from_config(None)["verbosity"], 3)


class PersonalityToolTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = MemoryStore(os.path.join(self.dir.name, "m.db"))
        self.addCleanup(self.store.close)
        self.personality = Personality(self.dir.name)

    def test_show_and_set_and_reset(self):
        tool = PersonalityTool(self.store, {}, self.personality)
        self.assertIn("playfulness", tool.invoke("show").content)
        result = tool.invoke("set", slider="warmth", value=5)
        self.assertTrue(result.ok)
        self.assertEqual(self.personality.values()["warmth"], 5)
        self.assertTrue(tool.invoke("reset").ok)
        self.assertEqual(self.personality.values()["warmth"], 3)

    def test_set_with_a_bad_value_says_what_is_wrong(self):
        tool = PersonalityTool(self.store, {}, self.personality)
        self.assertFalse(tool.invoke("set", slider="loudness", value=3).ok)
        self.assertFalse(tool.invoke("set", slider="warmth", value="warm").ok)

    def test_about_gathers_sliders_habits_and_facts(self):
        class Habits:
            def profile(self):
                return ["You often ask about code in the evening."]

        self.store.remember_fact("The user's favourite food is ramen.", "user")
        tool = PersonalityTool(self.store, {}, self.personality, Habits())
        text = tool.invoke("about").content
        self.assertIn("warmth", text)
        self.assertIn("evening", text)
        self.assertIn("ramen", text)

    def test_about_says_so_when_there_is_nothing_yet(self):
        text = PersonalityTool(self.store, {}, self.personality).invoke("about").content
        self.assertIn("Not enough yet", text)
        self.assertIn("haven't asked me to remember", text)

    def test_unknown_action_lists_the_real_ones(self):
        tool = PersonalityTool(self.store, {}, self.personality)
        result = tool.invoke("shout")
        self.assertFalse(result.ok)
        self.assertIn("show, about, set, reset", result.content)

    def test_preview_shows_the_change_before_the_gate_asks(self):
        tool = PersonalityTool(self.store, {}, self.personality)
        self.assertEqual(tool.preview("set", "warmth", 4), {"personality": "warmth: 4/5 (fond)"})
        self.assertIsNone(tool.preview("show"))


class PersonalityAgentTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = self.dir.name
        self.config = os.path.join(self.root, "config.toml")

    def write_config(self, extra=""):
        with open(self.config, "w") as handle:
            handle.write(f'[memory]\ndb_path = "{self.root}/mem.db"\n[upgrade]\ndir = "{self.root}/self"\n'
                         f'[habits]\ndir = "{self.root}/habits"\n{extra}')

    def test_a_moved_slider_reaches_his_prompt_and_his_tool(self):
        self.write_config('[personality]\nwarmth = 5\n')
        ai = BabyAI(config_path=self.config)
        self.addCleanup(ai.memory.close)
        self.assertIn("warmth", ai.context.build_messages()[0]["content"])
        self.assertIn("playfulness", ai.tool_registry.tools["personality"].invoke("show").content)

    def test_it_can_be_switched_off(self):
        self.write_config('[personality]\nenabled = false\n')
        ai = BabyAI(config_path=self.config)
        self.addCleanup(ai.memory.close)
        self.assertIsNone(ai.personality)
        self.assertNotIn("personality", ai.tool_registry.tools)
        self.assertEqual(ai.context.build_messages()[0]["content"], ai.context.system_prompt)


class PersonalityWiringTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def test_registry_offers_the_tool_only_when_the_sliders_are_on(self):
        personality = Personality(self.dir.name)
        with_tool = build_default_registry(config={}, personality=personality)
        without = build_default_registry(config={})
        self.assertIn("personality", with_tool.tools)
        self.assertNotIn("personality", without.tools)

    def test_moving_a_slider_is_the_users_call_but_reading_is_not(self):
        gate = SafetyGate(level="strict")
        self.assertTrue(gate.check("personality", {"action": "show"})["allowed"])
        self.assertTrue(gate.check("personality", {"action": "about"})["allowed"])
        self.assertTrue(gate.check("personality", {"action": "set", "slider": "warmth", "value": 5})["needs_confirmation"])

    def test_changing_back_to_the_starting_values_is_also_the_users_call(self):
        gate = SafetyGate(level="strict")
        self.assertTrue(gate.check("personality", {"action": "reset"})["needs_confirmation"])

    def test_a_file_with_the_wrong_sliders_is_not_trusted(self):
        path = os.path.join(self.dir.name, "personality.json")
        for values in ({"warmth": 5}, {"playfulness": 5, "warmth": 5, "curiosity": 5, "verbosity": 5, "volume": 5}):
            with open(path, "w") as handle:
                json.dump({"version": 1, "values": values}, handle)
            self.assertEqual(Personality(self.dir.name).values()["warmth"], 3, values)

    def test_a_broken_file_does_not_stop_him(self):
        path = os.path.join(self.dir.name, "personality.json")
        with open(path, "w") as handle:
            json.dump({"version": 1, "values": {"warmth": "very"}}, handle)
        self.assertEqual(Personality(self.dir.name).values()["warmth"], 3)


if __name__ == "__main__":
    unittest.main()
