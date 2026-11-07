"""He can upgrade himself: change his code on a branch, update, switch model, keep notes - and the user decides.

The things that must never give way (protected paths, tests before going live, the user's yes...) are pinned in
tests/test_guardrails.py, which he cannot edit. This file holds the rest of the behaviour, and he may edit it.

The code and update tests run real git in temporary repositories (a local "origin" stands in for GitHub); the model
tests talk to the fake Ollama in tests/fake_ollama.py."""


import tempfile
import threading
import unittest
from pathlib import Path

from babyai.agent.core import BabyAI
from babyai.llm.client import LLMClient
from babyai.safety.rules import UPGRADE_ACTION_RISK, RiskLevel, get_risk_level
from babyai.tools.registry import build_default_registry
from babyai.upgrade.code import MAX_FILES, CodeUpgrades
from babyai.upgrade.gitops import UpgradeError
from babyai.upgrade.models import ModelUpgrades, ollama_root, persist_model, set_toml_value
from babyai.upgrade.notes import MAX_NOTES, MAX_SKILLS, NOTE_MAX_CHARS, SKILL_MAX_CHARS, NoteBook
from tests.fake_ollama import FakeOllama
from tests.test_brain import ScriptedLLM, call_tool, say
from tests.test_guardrails import UNITTEST, AgentBase, CodeBase, UpdateBase, sh


class CodeUpgradeTests(CodeBase):
    def test_he_may_edit_his_own_upgrade_code_and_its_tests(self):
        folder = self.start()
        for path in ("babyai/upgrade/code.py", "babyai/upgrade/new_helper.py", "babyai/tools/upgrade.py", "tests/test_upgrade.py"):
            self.code.write(path, "x = 1\n")
            self.assertEqual((folder / path).read_text(), "x = 1\n", path)
        self.code.replace("babyai/upgrade/code.py", "x = 1", "x = 2")
        self.assertEqual((folder / "babyai" / "upgrade" / "code.py").read_text(), "x = 2\n")
        self.assertFalse((self.repo / "babyai" / "upgrade").exists())  # on the branch only

    def test_start_makes_a_branch_and_a_working_folder_and_leaves_the_live_checkout_alone(self):
        before = self.live_head()
        folder = self.start()
        proposal = self.code.journal.load()["proposal"]
        self.assertTrue(proposal["branch"].startswith("self/add-a-farewell-"))
        self.assertTrue((folder / "babyai" / "hello.py").exists())
        self.assertEqual(sh(self.repo, "git", "branch", "--list", proposal["branch"]).strip(" *+"), proposal["branch"])
        self.assertEqual(sh(self.repo, "git", "branch", "--show-current"), "main")
        self.assertEqual(self.live_head(), before)

    def test_only_one_change_at_a_time(self):
        self.start()
        with self.assertRaisesRegex(UpgradeError, "already in progress"):
            self.code.start("another")

    def test_a_goal_is_needed(self):
        with self.assertRaisesRegex(UpgradeError, "what the change is for"):
            self.code.start("   ")

    def test_writing_changes_the_branch_not_the_live_files(self):
        folder = self.start()
        self.code.write("babyai/bye.py", "def bye():\n    return 'bye'\n")
        self.assertEqual((folder / "babyai" / "bye.py").read_text(), "def bye():\n    return 'bye'\n")
        self.assertFalse((self.repo / "babyai" / "bye.py").exists())

    def test_replace_needs_the_text_exactly_once(self):
        folder = self.start()
        self.code.replace("babyai/hello.py", "'hi'", "'hello'")
        self.assertIn("'hello'", (folder / "babyai" / "hello.py").read_text())
        with self.assertRaisesRegex(UpgradeError, "appears 0 times"):
            self.code.replace("babyai/hello.py", "nothing like this", "x")
        self.code.write("babyai/two.py", "a = 1\na = 1\n")
        with self.assertRaisesRegex(UpgradeError, "appears 2 times"):
            self.code.replace("babyai/two.py", "a = 1", "a = 2")
        with self.assertRaisesRegex(UpgradeError, "doesn't exist"):
            self.code.replace("babyai/missing.py", "a", "b")

    def test_file_size_and_count_are_limited(self):
        self.start()
        with self.assertRaisesRegex(UpgradeError, "bigger than"):
            self.code.write("babyai/big.py", "x" * 300_000)
        for number in range(MAX_FILES):
            self.code.write(f"babyai/f{number}.py", "x = 1\n")
        with self.assertRaisesRegex(UpgradeError, "at most"):
            self.code.write("babyai/one_too_many.py", "x = 1\n")
        self.code.write("babyai/f0.py", "x = 2\n")  # changing a file already in the change is fine

    def test_diff_shows_the_change_and_saves_all_of_it(self):
        self.start()
        self.code.write("babyai/bye.py", "def bye():\n    return 'bye'\n")
        text = self.code.diff()
        self.assertIn("babyai/bye.py", text)
        self.assertIn("+def bye()", text)
        self.assertIn("NOT passed", text.replace("have NOT passed", "NOT passed"))
        saved = list((self.state / "proposals").glob("*.diff"))
        self.assertEqual(len(saved), 1)
        self.assertIn("+def bye()", saved[0].read_text())

    def test_diff_with_nothing_changed(self):
        self.start()
        self.assertIn("Nothing has changed", self.code.diff())

    def test_nothing_changed_means_nothing_to_test_or_apply(self):
        self.start()
        with self.assertRaisesRegex(UpgradeError, "nothing to test"):
            self.code.test()
        with self.assertRaisesRegex(UpgradeError, "nothing to apply"):
            self.code.apply()

    def test_an_untracked_file_such_as_config_toml_does_not_block_apply(self):
        self.start()
        self.code.write("babyai/bye.py", "x = 1\n")
        self.code.test()
        (self.repo / "config.toml").write_text("[llm]\nmodel = 'x'\n")
        self.assertIn("Applied", self.code.apply())
        self.assertTrue((self.repo / "config.toml").exists())

    def test_discard_removes_the_branch_and_the_folder(self):
        folder = self.start()
        branch = self.code.journal.load()["proposal"]["branch"]
        self.code.write("babyai/bye.py", "x = 1\n")
        self.code.discard()
        self.assertFalse(folder.exists())
        self.assertEqual(sh(self.repo, "git", "branch", "--list", branch), "")
        self.assertIsNone(self.code.journal.load()["proposal"])
        self.start("a fresh start")  # and a new one can begin

    def test_a_vanished_folder_drops_the_change(self):
        folder = self.start()
        import shutil

        shutil.rmtree(folder)
        with self.assertRaisesRegex(UpgradeError, "gone"):
            self.code.diff()
        self.assertIsNone(self.code.journal.load()["proposal"])

    def test_status_tells_where_things_are(self):
        self.assertIn("No change in progress", self.code.status())
        self.start("add a farewell")
        self.code.write("babyai/bye.py", "x = 1\n")
        self.assertIn("not tested yet", self.code.status())
        self.code.test()
        self.assertIn("passed", self.code.status())

    def test_it_will_not_run_outside_a_git_checkout(self):
        for repo in (None, self.root / "not-a-repo"):
            code = CodeUpgrades(repo, self.state)
            with self.assertRaisesRegex(UpgradeError, "git checkout"):
                code.start("anything")


class UpdateTests(UpdateBase):
    def test_up_to_date(self):
        self.assertIn("up to date", self.mine.check())
        self.assertIn("Already up to date", self.mine.update())

    def test_check_lists_what_is_new(self):
        self.publish({"babyai/new.py": "x = 1\n"}, "add new.py")
        text = self.mine.check()
        self.assertIn("1 new commit", text)
        self.assertIn("add new.py", text)
        self.assertNotEqual(self.head(), sh(self.upstream, "git", "rev-parse", "HEAD"))  # checking changes nothing

    def test_a_branch_that_tracks_nothing_says_so(self):
        sh(self.repo, "git", "checkout", "-q", "-b", "lonely")
        with self.assertRaisesRegex(UpgradeError, "isn't tracking"):
            self.mine.check()

    def test_rollback_goes_back_to_before_the_last_update(self):
        before = self.head()
        self.publish({"babyai/new.py": "x = 1\n"}, "add new.py")
        self.mine.update()
        self.assertEqual(self.mine.preview_rollback()["go back to"], before[:8])
        self.assertIn("Rolled back", self.mine.rollback())
        self.assertEqual(self.head(), before)
        self.assertFalse((self.repo / "babyai" / "new.py").exists())
        self.assertTrue(self.mine.journal.load()["applied"][-1]["rolled_back"])
        with self.assertRaisesRegex(UpgradeError, "no applied change"):
            self.mine.rollback()

    def test_rollback_of_a_code_change(self):
        code = CodeUpgrades(self.repo, self.state, UNITTEST)
        before = self.head()
        code.start("add a farewell")
        code.write("babyai/bye.py", "x = 1\n")
        code.test()
        code.apply()
        self.assertNotEqual(self.head(), before)
        self.mine.rollback()
        self.assertEqual(self.head(), before)

    def test_restart_runs_the_same_command_after_a_moment(self):
        done = threading.Event()
        said = self.mine.restart(delay=0.05, exec_fn=done.set)
        self.assertIn("Restarting", said)
        self.assertTrue(done.wait(2))


class TomlTests(unittest.TestCase):
    def test_replaces_the_value_in_place(self):
        text = '[memory]\ndb = "x"\n\n[llm]\nbase_url = "http://h/v1"\nmodel = "old"  # c\ntimeout = 5\n\n[ui]\nmodel = "not this"\n'
        out = set_toml_value(text, "llm", "model", "new:8b")
        self.assertIn('model = "new:8b"', out)
        self.assertIn('base_url = "http://h/v1"', out)
        self.assertIn('[ui]\nmodel = "not this"', out)
        self.assertEqual(out.count("old"), 0)

    def test_adds_the_key_to_an_existing_table(self):
        out = set_toml_value('[llm]\nbase_url = "x"\n', "llm", "model", "m")
        self.assertEqual(out, '[llm]\nmodel = "m"\nbase_url = "x"\n')

    def test_adds_the_table_when_missing_or_the_file_empty(self):
        self.assertEqual(set_toml_value('[ui]\na = 1\n', "llm", "model", "m"), '[ui]\na = 1\n\n[llm]\nmodel = "m"\n')
        self.assertEqual(set_toml_value("", "llm", "model", "m"), '[llm]\nmodel = "m"\n')

    def test_the_result_is_valid_toml_that_the_loader_reads(self):
        import tomllib

        for text in ("", '[llm]\nmodel = "a"\n', '[agent]\nname = "x"\n[llm]\ntimeout = 3\n'):
            self.assertEqual(tomllib.loads(set_toml_value(text, "llm", "model", "qwen2.5:7b"))["llm"]["model"], "qwen2.5:7b")

    def test_a_value_cannot_break_out_of_its_quotes(self):
        import tomllib

        for value in ('x"\nmodel = "evil', "back\\slash", 'quote"', "tab\there"):
            out = set_toml_value('[llm]\nbase_url = "x"\n', "llm", "model", value)
            parsed = tomllib.loads(out)
            self.assertEqual(parsed["llm"], {"model": value, "base_url": "x"}, value)

    def test_persist_writes_a_backup_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            path.write_text('[llm]\nmodel = "first"\n')
            persist_model(str(path), "second")
            persist_model(str(path), "third")
            self.assertIn('model = "third"', path.read_text())
            self.assertEqual((Path(folder) / "config.toml.bak").read_text(), '[llm]\nmodel = "first"\n')
            fresh = Path(folder) / "new.toml"
            persist_model(str(fresh), "m")
            self.assertEqual(fresh.read_text(), '[llm]\nmodel = "m"\n')


class Assistant:
    def __init__(self, base_url, model="llama3.1:8b"):
        self.llm_client = LLMClient(base_url=base_url, model=model, timeout=10)
        self._tools_ok = False


class ModelUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.config = str(Path(self.dir.name) / "config.toml")

    def tearDown(self):
        self.dir.cleanup()

    def upgrades(self, server, model="llama3.1:8b"):
        self.assistant = Assistant(server.url, model)
        return ModelUpgrades(self.assistant, self.config)

    def test_the_native_api_root_is_the_base_url_without_v1(self):
        self.assertEqual(ollama_root("http://localhost:11434/v1"), "http://localhost:11434")
        self.assertEqual(ollama_root("http://h:1/"), "http://h:1")

    def test_list_shows_sizes_tool_support_and_the_one_in_use(self):
        with FakeOllama(models=("llama3.1:8b", "tiny:1b"), no_tools=("tiny:1b",)) as server:
            text = self.upgrades(server).list()
        self.assertIn("llama3.1:8b (4.9 GB), can use tools <- in use", text)
        self.assertIn("tiny:1b (4.9 GB), CAN'T use tools", text)

    def test_pull_then_switch_changes_the_model_and_saves_it(self):
        with FakeOllama(models=("llama3.1:8b",), pullable=("qwen2.5:7b",)) as server:
            models = self.upgrades(server)
            self.assertIn("Pulled qwen2.5:7b", models.pull("qwen2.5:7b"))
            self.assertIn("Switched from llama3.1:8b to qwen2.5:7b", models.switch("qwen2.5:7b"))
            self.assertEqual(self.assistant.llm_client.model, "qwen2.5:7b")
            self.assertTrue(self.assistant._tools_ok)
            self.assertEqual(server.requests[-1]["model"], "qwen2.5:7b")  # it was tried with a real request
        self.assertIn('model = "qwen2.5:7b"', Path(self.config).read_text())

    def test_a_model_that_cannot_use_tools_is_refused(self):
        with FakeOllama(models=("llama3.1:8b", "tiny:1b"), no_tools=("tiny:1b",)) as server:
            models = self.upgrades(server)
            with self.assertRaisesRegex(UpgradeError, "can't use tools"):
                models.switch("tiny:1b")
        self.assertEqual(self.assistant.llm_client.model, "llama3.1:8b")
        self.assertFalse(Path(self.config).exists())

    def test_a_model_the_server_does_not_have_is_refused_with_a_hint(self):
        with FakeOllama(models=("llama3.1:8b",)) as server:
            with self.assertRaisesRegex(UpgradeError, "Pull it first"):
                self.upgrades(server).switch("mistral:7b")

    def test_a_model_that_fails_the_trial_is_not_switched_to(self):
        with FakeOllama(models=("llama3.1:8b", "odd:1b"), script=[{"role": "assistant", "content": ""}]) as server:
            models = self.upgrades(server)
            with self.assertRaisesRegex(UpgradeError, "answered with nothing"):
                models.switch("odd:1b")
        self.assertEqual(self.assistant.llm_client.model, "llama3.1:8b")
        self.assertFalse(Path(self.config).exists())

    def test_a_failed_pull_says_why(self):
        with FakeOllama() as server:
            with self.assertRaisesRegex(UpgradeError, "404"):
                self.upgrades(server).pull("nope:1b")

    def test_bad_model_names_are_refused_before_anything_is_asked_of_the_server(self):
        with FakeOllama(pullable=("bad name", "x;rm -rf /")) as server:
            models = self.upgrades(server)
            for name in ("", "bad name", "x;rm -rf /", "../../etc", '"quoted"', "line\nbreak", "a" * 200):
                with self.assertRaises(UpgradeError, msg=name):
                    models.pull(name)
                with self.assertRaises(UpgradeError, msg=name):
                    models.switch(name)
            self.assertEqual(server.pulls, [])
            self.assertEqual(server.requests, [])

    def test_an_unreachable_server_says_so(self):
        models = ModelUpgrades(Assistant("http://127.0.0.1:9/v1"), self.config)
        with self.assertRaisesRegex(UpgradeError, "couldn't reach the model server"):
            models.list()


class NoteBookTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.book = NoteBook(Path(self.dir.name) / "self")

    def tearDown(self):
        self.dir.cleanup()

    def test_notes_are_added_numbered_and_removed(self):
        self.assertEqual(self.book.notes(), [])
        self.book.add_note("Call the user boss.")
        self.book.add_note("Keep it   short\nalways")
        self.assertEqual(self.book.notes(), ["Call the user boss.", "Keep it short always"])  # one clean line each
        self.assertIn("already", self.book.add_note("Call the user boss."))
        self.assertIn("Removed note 1", self.book.remove_note(1))
        self.assertEqual(self.book.notes(), ["Keep it short always"])
        with self.assertRaisesRegex(UpgradeError, "no note number 5"):
            self.book.remove_note(5)

    def test_notes_survive_a_restart(self):
        self.book.add_note("Remember me")
        self.assertEqual(NoteBook(self.book.directory).notes(), ["Remember me"])

    def test_note_limits(self):
        with self.assertRaisesRegex(UpgradeError, "Say what"):
            self.book.add_note("  \n ")
        with self.assertRaisesRegex(UpgradeError, "at most"):
            self.book.add_note("x" * (NOTE_MAX_CHARS + 1))
        for number in range(MAX_NOTES):
            self.book.add_note(f"note {number}")
        with self.assertRaisesRegex(UpgradeError, "already have"):
            self.book.add_note("one more")

    def test_skills_are_saved_read_listed_and_deleted(self):
        self.assertIn("Saved", self.book.save_skill("morning-checkin", "1. Say hi\n2. Ask about sleep"))
        self.assertIn("Updated", self.book.save_skill("morning-checkin", "1. Say hi"))
        self.assertEqual(self.book.show_skill("morning-checkin"), "1. Say hi")
        self.assertEqual(self.book.skills(), ["morning-checkin"])
        self.book.delete_skill("morning-checkin")
        self.assertEqual(self.book.skills(), [])
        with self.assertRaisesRegex(UpgradeError, "don't have a skill"):
            self.book.show_skill("morning-checkin")

    def test_skill_limits(self):
        with self.assertRaisesRegex(UpgradeError, "Say what"):
            self.book.save_skill("empty", "  ")
        with self.assertRaisesRegex(UpgradeError, "at most"):
            self.book.save_skill("long", "x" * (SKILL_MAX_CHARS + 1))
        for number in range(MAX_SKILLS):
            self.book.save_skill(f"skill-{number}", "steps")
        with self.assertRaisesRegex(UpgradeError, "already have"):
            self.book.save_skill("one-more", "steps")
        self.book.save_skill("skill-0", "better steps")  # changing one he has is fine
        self.assertEqual(self.book.show_skill("skill-0"), "better steps")

    def test_skill_names_cannot_escape_the_folder(self):
        for name in ("../notes", "a/b", "", "UP per", ".hidden", "x" * 60):
            with self.assertRaises(UpgradeError, msg=name):
                self.book.save_skill(name, "text")
        self.assertFalse((self.book.directory.parent / "notes.md").exists())

    def test_the_prompt_block_is_empty_with_nothing_and_never_claims_to_override_the_rules(self):
        self.assertEqual(self.book.prompt_block(), "")
        self.book.add_note("Call the user boss.")
        self.book.save_skill("checkin", "ask about the day")
        block = self.book.prompt_block()
        self.assertIn("- Call the user boss.", block)
        self.assertIn("checkin", block)
        self.assertTrue(block.rstrip().endswith("the rules always win."))


class GateTests(unittest.TestCase):
    def test_looking_and_working_on_a_branch_are_free(self):
        for tool, action in (("upgrade_code", "start"), ("upgrade_code", "write"), ("upgrade_code", "diff"), ("upgrade_version", "check"),
                             ("upgrade_model", "list"), ("upgrade_notes", "show")):
            self.assertEqual(get_risk_level(tool, {"action": action}), RiskLevel.LOW, (tool, action))

    def test_every_action_a_tool_offers_has_a_rating(self):
        import re

        from babyai.tools.upgrade import UpgradeCodeTool, UpgradeModelTool, UpgradeNotesTool, UpgradeVersionTool

        for tool in (UpgradeCodeTool(), UpgradeModelTool(), UpgradeNotesTool(), UpgradeVersionTool()):
            class Recorder:
                def __getattr__(self, name):
                    return lambda *a, **k: (_ for _ in ()).throw(UpgradeError("stub"))

            tool.upgrade = type("U", (), {"code": Recorder(), "version": Recorder(), "models": Recorder(), "notes": Recorder()})()
            probe = tool.invoke(action="no-such-action")
            listed = re.search(r"Actions: (.+)\.$", probe.content).group(1).split(", ")
            self.assertEqual(set(listed), set(UPGRADE_ACTION_RISK[tool.name]), tool.name)


class AgentTests(AgentBase):
    def test_the_four_tools_exist_and_can_be_switched_off(self):
        self.assertTrue({"upgrade_code", "upgrade_version", "upgrade_model", "upgrade_notes"} <= set(self.ai.tool_registry.tools))
        self.write_config(extra="enabled = false\n")
        off = BabyAI(config_path=self.config)
        self.assertIsNone(off.upgrade)
        self.assertFalse([name for name in off.tool_registry.tools if name.startswith("upgrade_")])
        self.assertEqual(off.context.build_messages()[0]["content"], off.context.system_prompt)

    def test_the_default_registry_has_none_of_them(self):
        self.assertFalse([name for name in build_default_registry().tools if name.startswith("upgrade_")])

    def test_with_their_yes_the_note_is_kept_and_joins_the_next_prompt(self):
        self.ai.confirm = lambda *args: True
        self.ai.llm_client = ScriptedLLM(self.note(), say("Done, boss."), say("Sure."))
        self.ai.process_user_input("call me boss")
        self.assertEqual(self.ai.upgrade.notes.notes(), ["Call the user boss."])
        self.ai.process_user_input("hello")
        system = self.ai.llm_client.calls[-1]["messages"][0]["content"]
        self.assertTrue(system.startswith(self.ai.context.system_prompt))
        self.assertIn("- Call the user boss.", system)
        self.assertTrue(system.rstrip().endswith("the rules always win."))

    def test_looking_needs_no_yes(self):
        self.ai.confirm = lambda *args: self.fail("looking should not ask")
        self.ai.llm_client = ScriptedLLM(call_tool("upgrade_notes", {"action": "show"}), say("No notes yet."))
        self.assertEqual(self.ai.process_user_input("what notes do you have"), "No notes yet.")

    def test_a_working_session_can_take_several_steps_in_one_message(self):
        self.ai.confirm = lambda *args: True
        self.ai.llm_client = ScriptedLLM(
            call_tool("upgrade_notes", {"action": "show"}, "a"),
            call_tool("upgrade_notes", {"action": "add_note", "text": "Be brief."}, "b"),
            say("I added a note."),
        )
        self.assertEqual(self.ai.process_user_input("note that I like short answers"), "I added a note.")
        calls = self.ai.llm_client.calls
        self.assertEqual(len(calls), 3)
        self.assertIsNotNone(calls[1]["tools"])  # mid-work, the follow-up may call tools again
        self.assertEqual(self.ai.upgrade.notes.notes(), ["Be brief."])
        roles = [m["role"] for m in calls[2]["messages"]]
        self.assertEqual(roles.count("tool"), 2)

    def test_other_tools_still_get_a_single_plain_follow_up(self):
        path = self.root / "a.txt"
        path.write_text("needle")
        self.ai.llm_client = ScriptedLLM(call_tool("read_file", {"path": str(path)}), say("It says needle."))
        self.ai.process_user_input("read it")
        self.assertIsNone(self.ai.llm_client.calls[1]["tools"])
