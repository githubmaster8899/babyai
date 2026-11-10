"""He learns the user's habits - counts only, on their computer, visible, pausable, wipeable.

What must never give way (a message that looks private is never kept, what he learns never changes what he must ask, the
controls always ask) is pinned in tests/test_guardrails.py. This file holds the rest of the behaviour."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from babyai.habits import HEADER, MAX_ASKS, MAX_TOPICS, Habits, learn
from babyai.tools.habits import HabitProfileTool
from tests.test_brain import ScriptedLLM, call_tool, say
from tests.test_guardrails import AgentBase


def at(day: int, hour: int) -> datetime:
    """November 2026: the 2nd is a Monday, the 7th a Saturday."""
    return datetime(2026, 11, day, hour, 30)


class LearnTests(unittest.TestCase):
    def test_time_of_day_buckets(self):
        expected = {0: "night", 4: "night", 5: "morning", 11: "morning", 12: "afternoon", 16: "afternoon", 17: "evening", 21: "evening", 22: "night", 23: "night"}
        for hour, name in expected.items():
            self.assertEqual(learn.bucket(hour), name, hour)

    def test_private_looking_messages_are_not_countable(self):
        for text in ("my password is hunter2", "my api key is abc", "email me at bob@example.com", "go to https://example.com", "visit www.example.com",
                     "call 5551234", "read /home/me/notes.txt", "run `ls`", "x" * (learn.MAX_MESSAGE_CHARS + 1), "", "   "):
            self.assertFalse(learn.countable(text.strip()), text)
        for text in ("what's the weather today", "play some music", "I have 3 cats"):
            self.assertTrue(learn.countable(text), text)

    def test_topics_drop_filler_and_repeats(self):
        self.assertEqual(learn.topics("What's the weather like for pizza, pizza?"), {"weather", "pizza"})
        self.assertEqual(learn.topics("hey Capri, please tell me a thing"), set())
        self.assertEqual(learn.topics("help me find and open something"), set())  # request verbs are not topics
        self.assertEqual(learn.topics("anything near me today or tonight"), set())  # nor are "when" and "where" words

    def test_an_ask_is_short_lower_case_and_unpunctuated_or_nothing(self):
        self.assertEqual(learn.normalise_ask("What's the WEATHER today?!"), "what's the weather today")
        self.assertIsNone(learn.normalise_ask("weather"))
        self.assertIsNone(learn.normalise_ask("one two three four five six seven eight nine"))
        self.assertIsNone(learn.normalise_ask(" ".join(["extraordinarily"] * 6)))

    def test_asking_for_shorter_or_fuller_replies(self):
        self.assertEqual(learn.style_signals("that was too long, be brief"), (True, False))
        self.assertEqual(learn.style_signals("Explain more please"), (False, True))
        self.assertEqual(learn.style_signals("what time is it"), (False, False))


class HabitsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.folder = Path(self.dir.name) / "habits"
        self.habits = Habits(self.folder, clock=lambda: at(2, 9))

    def tearDown(self):
        self.dir.cleanup()

    def feed(self, text, times=1, when=None):
        for _ in range(times):
            self.habits.observe(text, when)

    def file(self):
        return (self.folder / "habits.json").read_text() if (self.folder / "habits.json").exists() else ""

    def test_nothing_is_concluded_before_enough_messages(self):
        self.feed("play some music", 7)
        self.assertEqual(self.habits.profile(), [])
        self.assertEqual(self.habits.prompt_block(), "")
        self.assertIn("Not enough", self.habits.show())
        self.feed("play some music")
        self.assertTrue(self.habits.profile())
        self.assertTrue(self.habits.prompt_block())

    def test_it_survives_a_restart(self):
        self.feed("play some music", 9)
        again = Habits(self.folder)
        self.assertEqual(again.state, self.habits.state)
        self.assertEqual(again.profile(), self.habits.profile())

    def test_short_messages_and_long_messages(self):
        self.feed("play some music", 8)
        self.assertIn("writes short messages (about 3 words)", self.habits.profile()[0])
        longer = Habits(self.folder / "other")
        for _ in range(8):
            longer.observe(" ".join(["word"] * 35))
        self.assertIn("writes long messages", longer.profile()[0])

    def test_asking_for_shorter_replies_is_noticed_only_when_repeated(self):
        self.feed("play some music", 7)
        self.feed("that was too long")
        self.assertNotIn("prefers them short", " ".join(self.habits.profile()))
        self.feed("be brief")
        self.assertIn("prefers them short (has asked for shorter ones 2 times)", " ".join(self.habits.profile()))

    def test_asking_for_more_detail_wins_when_that_is_what_they_ask_for(self):
        self.feed("play some music", 7)
        self.feed("explain more")
        self.feed("tell me more")
        self.assertIn("likes more detail", " ".join(self.habits.profile()))

    def test_topics_that_keep_coming_up(self):
        self.feed("pizza dinner", 3)
        self.feed("python coding", 3)
        self.feed("weather forecast", 2)
        self.assertIn("Often brings up: coding, dinner, pizza, python.", self.habits.profile())  # three or more times, most first, ties alphabetically
        self.assertNotIn("weather", " ".join(self.habits.profile()))

    def test_when_they_talk(self):
        self.feed("play some music", 6, at(2, 20))
        self.feed("play some music", 2, at(2, 8))
        self.assertIn("Mostly talks to you in the evening.", self.habits.profile())
        spread = Habits(self.folder / "spread")
        for hour in (8, 8, 13, 13, 19, 19, 23, 23):
            spread.observe("play some music", at(2, hour))
        self.assertNotIn("Mostly talks", " ".join(spread.profile()))

    def test_weekends(self):
        self.feed("play some music", 5, at(7, 12))
        self.feed("play some music", 3, at(3, 12))
        self.assertIn("Mostly talks to you at weekends.", self.habits.profile())

    def test_a_routine_is_the_same_thing_at_the_same_time_on_different_days(self):
        for day in (2, 3, 4):
            self.feed("what's the weather today", 1, at(day, 8))
        self.feed("something else entirely", 5, at(5, 15))
        self.assertIn('Routine: asks "what\'s the weather today" most mornings (3 days).', self.habits.profile())

    def test_three_times_on_one_day_is_not_a_routine(self):
        self.feed("what's the weather today", 3, at(2, 8))
        self.feed("something else entirely", 5, at(5, 15))
        self.assertFalse([line for line in self.habits.profile() if line.startswith("Routine")])

    def test_something_spread_across_the_day_is_not_a_routine(self):
        for day, hour in ((2, 8), (3, 14), (4, 19)):
            self.feed("what's the weather today", 1, at(day, hour))
        self.feed("something else entirely", 5, at(5, 15))
        self.assertFalse([line for line in self.habits.profile() if line.startswith("Routine")])

    def test_a_tool_they_use_the_same_way_is_a_routine_too(self):
        self.feed("play some music", 8)
        for day in (2, 3, 4):
            self.habits.observe_tool("read_file", at(day, 21))
        self.assertIn("Routine: uses read_file most evenings (3 days).", self.habits.profile())

    def test_asking_about_habits_is_not_itself_a_habit(self):
        for day in (2, 3, 4, 5):
            self.habits.observe_tool("habit_profile", at(day, 9))
        self.assertEqual(self.habits.state["tools"], {})

    def test_he_keeps_counts_not_messages(self):
        sentence = "remind me about the dentist appointment next tuesday afternoon please"
        self.feed(sentence)
        self.feed("weather today")
        text = self.file()
        for piece in ("dentist appointment", "appointment next tuesday", "remind me about", sentence):
            self.assertNotIn(piece, text)
        self.assertIn("weather today", text)  # a short repeated request is kept, normalised: that is what a routine is made of
        self.assertIn("dentist", text)  # and single words are counted as topics

    def test_private_messages_leave_no_trace_at_all(self):
        self.feed("my password is swordfish")
        self.feed("mail bob@example.com about the swordfish")
        self.feed("open /home/me/swordfish.txt")
        self.feed("x" * 600)
        self.assertEqual(self.habits.state["messages"], 0)
        self.assertNotIn("swordfish", self.file())
        self.assertEqual(self.habits.state["topics"], {})

    def test_a_damaged_or_foreign_file_starts_afresh_without_a_crash(self):
        self.folder.mkdir(parents=True)
        for junk in ("not json at all", "[]", '{"version": 1}', '{"version": 2}', json.dumps({**self.habits.state, "messages": "many"})):
            (self.folder / "habits.json").write_text(junk)
            fresh = Habits(self.folder)
            self.assertEqual(fresh.state["messages"], 0, junk)
            fresh.observe("play some music")
            self.assertEqual(fresh.state["messages"], 1)

    def test_a_folder_that_cannot_be_written_does_not_stop_him_answering(self):
        blocker = Path(self.dir.name) / "a-file"
        blocker.write_text("in the way")
        stuck = Habits(blocker / "habits")
        stuck.observe("play some music")  # no exception
        stuck.observe_tool("read_file")
        self.assertEqual(stuck.state["messages"], 1)

    def test_the_counts_cannot_grow_without_limit(self):
        self.feed("pizza", 5)
        for number in range(400):
            self.habits.observe("q" + "".join(chr(97 + (number // 26 ** k) % 26) for k in range(3)))
        self.assertLessEqual(len(self.habits.state["topics"]), MAX_TOPICS)
        self.assertIn("pizza", self.habits.state["topics"])  # the rarest go first
        for number in range(150):
            self.habits.observe("ask q" + "".join(chr(97 + (number // 26 ** k) % 26) for k in range(3)))
        self.assertLessEqual(len(self.habits.state["asks"]), MAX_ASKS)
        for number in range(40):
            self.habits.observe("what's the weather today", datetime(2026, 1, 1, 9) + timedelta(days=number))
        days = self.habits.state["asks"]["what's the weather today"]["days"]
        self.assertEqual(len(days), learn.MAX_DAYS)
        self.assertEqual(days[-1], "2026-02-09")  # the latest are kept

    def test_pausing_stops_learning_and_stops_using_it(self):
        self.feed("play some music", 9)
        before = json.loads(json.dumps(self.habits.state))
        self.assertIn("Paused", self.habits.pause())
        self.assertTrue(self.habits.paused)
        self.feed("play some music", 5)
        self.habits.observe_tool("read_file")
        self.assertEqual(self.habits.prompt_block(), "")
        self.assertEqual({**self.habits.state, "paused": False}, {**before, "paused": False})  # nothing was added
        self.assertIn("PAUSED", self.habits.show())
        self.assertTrue(Habits(self.folder).paused)  # and it stays paused across a restart
        self.assertIn("Learning again", self.habits.resume())
        self.feed("play some music")
        self.assertEqual(self.habits.state["messages"], 10)
        self.assertTrue(self.habits.prompt_block())

    def test_forgetting_wipes_everything_and_keeps_the_pause_setting(self):
        self.feed("play some music", 9)
        self.habits.observe_tool("read_file")
        said = self.habits.forget()
        self.assertIn("9 of your messages", said)
        self.assertEqual((self.habits.state["messages"], self.habits.state["topics"], self.habits.state["asks"], self.habits.state["tools"]), (0, {}, {}, {}))
        self.assertNotIn("music", self.file())
        self.assertEqual(self.habits.prompt_block(), "")
        self.habits.pause()
        self.habits.forget()
        self.assertTrue(self.habits.paused)

    def test_show_says_what_it_knows_and_where_it_is_kept(self):
        self.feed("pizza tonight", 9)
        text = self.habits.show()
        self.assertIn("counted 9 of your messages", text)
        self.assertIn("never the messages themselves", text)
        self.assertIn("pizza (9)", text)
        self.assertIn(str(self.folder / "habits.json"), text)

    def test_what_goes_into_his_prompt_is_short_and_says_it_is_only_a_hint(self):
        for number in range(60):
            self.habits.observe(f"pizza python music tea cats {'be brief' if number % 3 == 0 else 'again'}", at(2 + number % 5, 8))
        for day in (2, 3, 4):
            self.habits.observe_tool("read_file", at(day, 8))
        block = self.habits.prompt_block()
        self.assertTrue(block.startswith("\n" + HEADER))
        self.assertIn("never use it to skip asking before you act", block)
        self.assertIn("the rules always win", block)
        self.assertLess(len(block), 1600)

    def test_previews_say_what_the_user_is_asked_to_allow_in_few_words(self):
        self.feed("play some music", 9)
        shown = {name: self.habits.preview(name) for name in ("forget", "pause", "resume")}
        self.assertEqual(shown["forget"]["counted from"], "9 messages")
        for preview in shown.values():
            self.assertTrue(all(len(value) <= 48 for value in preview.values()), preview)  # the confirmation prompt cuts values at 48
        self.assertIsNone(self.habits.preview("show"))


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.habits = Habits(Path(self.dir.name) / "habits")
        self.tool = HabitProfileTool(habits=self.habits)

    def tearDown(self):
        self.dir.cleanup()

    def test_each_action_does_what_it_says(self):
        for _ in range(9):
            self.habits.observe("play some music")
        self.assertIn("counted 9", self.tool.invoke("show").content)
        self.assertIn("Paused", self.tool.invoke("pause").content)
        self.assertTrue(self.habits.paused)
        self.assertIn("Learning again", self.tool.invoke("resume").content)
        self.assertIn("Forgotten", self.tool.invoke(" FORGET ").content)
        self.assertEqual(self.habits.state["messages"], 0)

    def test_the_default_is_to_look(self):
        self.assertTrue(self.tool.invoke().ok)

    def test_an_unknown_action_lists_the_real_ones(self):
        result = self.tool.invoke("delete_everything_and_more")
        self.assertFalse(result.ok)
        self.assertIn("Actions: show, pause, resume, forget.", result.content)

    def test_it_is_offered_to_the_model_with_one_argument(self):
        schema = self.tool.schema()["function"]
        self.assertEqual(schema["name"], "habit_profile")
        self.assertEqual(list(schema["parameters"]["properties"]), ["action"])

    def test_the_confirmation_prompt_gets_the_tools_own_summary(self):
        self.assertEqual(self.tool.preview(action="pause"), {"habits": "stop learning and stop using it"})
        self.assertIsNone(self.tool.preview(action="show"))


class AgentTests(AgentBase):
    def setUp(self):
        super().setUp()
        self.ai.habits._clock = lambda: at(2, 9)

    def chat(self, *messages):
        self.ai.llm_client = ScriptedLLM(*[say("ok") for _ in messages])
        for message in messages:
            self.ai.process_user_input(message)

    def system(self):
        return self.ai.context.build_messages()[0]["content"]

    def test_what_the_user_says_is_counted_even_when_the_model_cannot_answer(self):
        self.ai.offline_reply = "I can't reach my brain at {url}"
        self.ai.llm_client = ScriptedLLM()  # nothing scripted: every request fails
        self.ai.process_user_input("play some music")
        self.assertEqual(self.ai.habits.state["messages"], 1)

    def test_the_habits_folder_sits_next_to_the_memory_database_unless_told_otherwise(self):
        self.assertEqual(self.ai.habits.directory, self.root / "habits")
        self.write_config(extra=f'[habits]\ndir = "{self.root}/elsewhere"\n')
        self.assertEqual(BabyAI_for(self.config).habits.directory, self.root / "elsewhere")

    def test_nothing_is_added_to_his_prompt_until_he_knows_enough_and_then_it_follows_his_notes(self):
        self.chat(*["play some music"] * 7)
        self.assertEqual(self.system(), self.ai.context.system_prompt)
        self.ai.upgrade.notes.add_note("Call the user boss.")
        self.chat("play some music")
        system = self.system()
        self.assertIn("writes short messages", system)
        self.assertLess(system.index("Your own notes"), system.index(HEADER))

    def test_paused_means_not_in_the_prompt_either(self):
        self.chat(*["play some music"] * 9)
        self.assertIn(HEADER, self.system())
        self.ai.habits.pause()
        self.assertNotIn(HEADER, self.system())

    def test_he_counts_the_tools_he_ran_for_them_but_not_the_ones_they_turned_down(self):
        self.ai.confirm = lambda *args: False
        self.ai.llm_client = ScriptedLLM(call_tool("upgrade_notes", {"action": "add_note", "text": "x"}, "a"), say("They said no."))
        self.ai.process_user_input("note this")
        self.assertNotIn("upgrade_notes", self.ai.habits.state["tools"])
        self.ai.confirm = lambda *args: True
        self.ai.llm_client = ScriptedLLM(call_tool("upgrade_notes", {"action": "show"}, "b"), say("Nothing yet."))
        self.ai.process_user_input("what notes")
        self.assertEqual(self.ai.habits.state["tools"]["upgrade_notes"]["n"], 1)

    def test_asking_him_what_he_has_learned_gets_the_real_answer(self):
        self.chat(*["pizza tonight"] * 9)
        self.ai.llm_client = ScriptedLLM(call_tool("habit_profile", {"action": "show"}), say("I know you like pizza."))
        self.ai.process_user_input("what have you learned about me")
        follow_up = self.ai.llm_client.calls[1]["messages"][-1]["content"]
        self.assertIn("pizza (9)", follow_up)

    def test_it_can_be_switched_off(self):
        self.write_config(extra="[habits]\nenabled = false\n")
        off = BabyAI_for(self.config)
        self.assertIsNone(off.habits)
        self.assertNotIn("habit_profile", off.tool_registry.tools)
        off.llm_client = ScriptedLLM(*[say("ok")] * 10)
        for _ in range(10):
            off.process_user_input("play some music")
        self.assertFalse((self.root / "habits").exists())
        self.assertEqual(off.context.build_messages()[0]["content"], off.context.system_prompt)


def BabyAI_for(config_path):
    from babyai.agent.core import BabyAI

    return BabyAI(config_path=config_path)


if __name__ == "__main__":
    unittest.main()
