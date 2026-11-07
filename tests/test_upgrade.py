"""He can upgrade himself: change his code on a branch, update, switch model, keep notes - and the user decides.

The code and update tests run real git in temporary repositories (a local "origin" stands in for GitHub); the model
tests talk to the fake Ollama in tests/fake_ollama.py."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from babyai.agent.core import MAX_ROUNDS, MAX_TOOL_CALLS, MAX_TOOL_CALLS_TOTAL, BabyAI
from babyai.llm.client import LLMClient
from babyai.safety.gate import SafetyGate
from babyai.safety.rules import UPGRADE_ACTION_RISK, RiskLevel, get_risk_level
from babyai.tools.registry import build_default_registry
from babyai.upgrade import UpgradeContext
from babyai.upgrade.code import MAX_FILES, PROTECTED, CodeUpgrades, is_protected
from babyai.upgrade.gitops import Journal, UpgradeError, git
from babyai.upgrade.models import ModelUpgrades, ollama_root, persist_model, set_toml_value
from babyai.upgrade.notes import MAX_NOTES, MAX_SKILLS, NOTE_MAX_CHARS, SKILL_MAX_CHARS, NoteBook
from babyai.upgrade.update import VersionUpdates
from tests.fake_ollama import FakeOllama
from tests.test_brain import ScriptedLLM, call_tool, say

UNITTEST = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."]
IDENT = ("-c", "user.name=Test", "-c", "user.email=test@example.com")


def sh(cwd, *args):
    done = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)
    assert done.returncode == 0, f"{args}: {done.stdout}{done.stderr}"
    return done.stdout.strip()


def commit_all(repo, message):
    sh(repo, "git", "add", "-A")
    sh(repo, "git", *IDENT, "commit", "-q", "-m", message)


def make_repo(root: Path) -> Path:
    """A small repository shaped like his own: a package, a protected safety module, licence files, passing tests."""
    repo = root / "repo"
    files = {
        "babyai/__init__.py": "",
        "babyai/hello.py": "def hello():\n    return 'hi'\n",
        "babyai/safety/__init__.py": "",
        "babyai/safety/rules.py": "RISK = 1\n",
        "NOTICE": "notice\n",
        "capri/LICENSE.md": "licence\n",
        "tests/__init__.py": "",
        "tests/test_hello.py": "import unittest\nfrom babyai.hello import hello\n\n\nclass T(unittest.TestCase):\n    def test_hello(self):\n        self.assertEqual(hello(), 'hi')\n",
    }
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    sh(repo, "git", "init", "-q", "-b", "main")
    commit_all(repo, "start")
    return repo


class Workspace(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.repo = make_repo(self.root)
        self.state = self.root / "state"

    def tearDown(self):
        self.dir.cleanup()


# --------------------------------------------------------------------------- what may be edited


class ProtectedPathTests(unittest.TestCase):
    def test_the_safety_upgrade_and_licence_files_are_protected(self):
        for path in ("babyai/safety/gate.py", "babyai/safety/rules.py", "babyai/upgrade/code.py", "babyai/tools/upgrade.py",
                     "babyai/agent/core.py", "NOTICE", "LICENSE", "capri/LICENSE.md", "tests/test_safety.py", "tests/test_upgrade.py",
                     ".git/config", "BABYAI/SAFETY/rules.py", "sub/.git/hooks/pre-commit"):
            self.assertTrue(is_protected(path), path)

    def test_ordinary_files_are_not(self):
        for path in ("babyai/tools/clock.py", "babyai/ui/app.py", "tests/test_clock.py", "babyai/safetyish.py", "docs/NOTICE.md"):
            self.assertFalse(is_protected(path), path)

    def test_every_rule_is_something_that_exists_in_the_repo(self):
        root = Path(__file__).resolve().parents[1]
        for rule in PROTECTED:
            if rule == ".git/" or (rule == "LICENSE" and not (root / rule).exists()):
                continue  # .git is a rule, not a file of the repo; this checkout may not have a LICENSE
            self.assertTrue((root / rule.rstrip("/")).exists(), f"{rule} is listed as protected but is not in the repo")


# --------------------------------------------------------------------------- code, on a branch


class CodeUpgradeTests(Workspace):
    def setUp(self):
        super().setUp()
        self.code = CodeUpgrades(self.repo, self.state, UNITTEST)

    def start(self, goal="add a farewell"):
        self.code.start(goal)
        return Path(self.code.journal.load()["proposal"]["path"])

    def live_head(self):
        return sh(self.repo, "git", "rev-parse", "HEAD")

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

    def test_protected_files_cannot_be_written_or_edited(self):
        self.start()
        for path in ("babyai/safety/rules.py", "babyai/safety/new.py", "NOTICE", "capri/LICENSE.md", "babyai/upgrade/code.py"):
            with self.assertRaisesRegex(UpgradeError, "protected"):
                self.code.write(path, "x")
        with self.assertRaisesRegex(UpgradeError, "protected"):
            self.code.replace("babyai/safety/rules.py", "RISK = 1", "RISK = 0")

    def test_paths_cannot_leave_the_working_folder(self):
        folder = self.start()
        for path in ("../outside.txt", "/etc/passwd", "babyai/../../outside.txt", "", "   "):
            with self.assertRaises(UpgradeError, msg=path):
                self.code.write(path, "x")
        outside = self.root / "elsewhere"
        outside.mkdir()
        (folder / "link").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(UpgradeError, "outside"):
            self.code.write("link/escape.txt", "x")
        self.assertFalse((outside / "escape.txt").exists())

    def test_a_write_cannot_go_through_a_symlink_into_a_protected_file(self):
        folder = self.start()
        (folder / "babyai" / "sneaky.py").symlink_to(folder / "NOTICE")
        with self.assertRaisesRegex(UpgradeError, "protected"):
            self.code.write("babyai/sneaky.py", "changed")
        self.assertEqual((self.repo / "NOTICE").read_text(), "notice\n")
        self.assertEqual((folder / "NOTICE").read_text(), "notice\n")

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

    def test_the_whole_workflow_makes_the_change_live_after_passing_tests(self):
        before = self.live_head()
        self.start("add a farewell")
        self.code.write("babyai/bye.py", "def bye():\n    return 'bye'\n")
        self.code.write("tests/test_bye.py", "import unittest\nfrom babyai.bye import bye\n\n\nclass T(unittest.TestCase):\n    def test_bye(self):\n        self.assertEqual(bye(), 'bye')\n")
        self.assertIn("passed", self.code.test())
        self.assertIn("Applied", self.code.apply())
        self.assertNotEqual(self.live_head(), before)
        self.assertEqual((self.repo / "babyai" / "bye.py").read_text(), "def bye():\n    return 'bye'\n")
        self.assertEqual(sh(self.repo, "git", "branch", "--show-current"), "main")
        self.assertEqual(sh(self.repo, "git", "status", "--porcelain"), "")
        data = self.code.journal.load()
        self.assertIsNone(data["proposal"])
        self.assertEqual([(e["kind"], e["before"], e["after"]) for e in data["applied"]], [("code", before, self.live_head())])
        self.assertIn(data["applied"][0]["branch"], sh(self.repo, "git", "branch", "--list", "self/*"))  # kept, for the record
        self.assertFalse(list((self.state / "worktrees").glob("*")))  # the working folder is gone

    def test_nothing_goes_live_before_the_tests_have_passed(self):
        self.start()
        self.code.write("babyai/bye.py", "x = 1\n")
        with self.assertRaisesRegex(UpgradeError, "haven't passed"):
            self.code.apply()
        self.assertFalse((self.repo / "babyai" / "bye.py").exists())

    def test_failing_tests_block_apply(self):
        self.start()
        self.code.write("babyai/hello.py", "def hello():\n    return 'broken'\n")
        self.assertIn("FAILED", self.code.test())
        with self.assertRaisesRegex(UpgradeError, "haven't passed"):
            self.code.apply()
        self.assertEqual((self.repo / "babyai" / "hello.py").read_text(), "def hello():\n    return 'hi'\n")

    def test_editing_after_the_tests_means_they_must_run_again(self):
        self.start()
        self.code.write("babyai/bye.py", "x = 1\n")
        self.assertIn("passed", self.code.test())
        self.code.write("babyai/bye.py", "x = 2\n")  # a different version from the one that was tested
        with self.assertRaisesRegex(UpgradeError, "haven't passed"):
            self.code.apply()
        self.assertIn("passed", self.code.test())
        self.assertIn("Applied", self.code.apply())

    def test_nothing_changed_means_nothing_to_test_or_apply(self):
        self.start()
        with self.assertRaisesRegex(UpgradeError, "nothing to test"):
            self.code.test()
        with self.assertRaisesRegex(UpgradeError, "nothing to apply"):
            self.code.apply()

    def test_uncommitted_work_in_the_live_checkout_blocks_apply(self):
        self.start()
        self.code.write("babyai/bye.py", "x = 1\n")
        self.code.test()
        (self.repo / "babyai" / "hello.py").write_text("def hello():\n    return 'edited by hand'\n")
        with self.assertRaisesRegex(UpgradeError, "uncommitted"):
            self.code.apply()
        self.assertEqual((self.repo / "babyai" / "hello.py").read_text(), "def hello():\n    return 'edited by hand'\n")

    def test_an_untracked_file_such_as_config_toml_does_not_block_apply(self):
        self.start()
        self.code.write("babyai/bye.py", "x = 1\n")
        self.code.test()
        (self.repo / "config.toml").write_text("[llm]\nmodel = 'x'\n")
        self.assertIn("Applied", self.code.apply())
        self.assertTrue((self.repo / "config.toml").exists())

    def test_if_the_checkout_moved_since_the_change_started_it_is_not_applied(self):
        self.start()
        self.code.write("babyai/bye.py", "x = 1\n")
        self.code.test()
        (self.repo / "other.txt").write_text("a new commit\n")
        commit_all(self.repo, "moved on")
        with self.assertRaisesRegex(UpgradeError, "moved on"):
            self.code.apply()

    def test_a_protected_file_smuggled_into_the_branch_is_still_refused_at_apply(self):
        folder = self.start()
        self.code.write("babyai/bye.py", "x = 1\n")
        (folder / "babyai" / "safety" / "rules.py").write_text("RISK = 0\n")  # not through the tools
        self.code.test()
        with self.assertRaisesRegex(UpgradeError, "babyai/safety/rules.py"):
            self.code.apply()
        self.assertEqual((self.repo / "babyai" / "safety" / "rules.py").read_text(), "RISK = 1\n")

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

    def test_nothing_is_pushed_and_no_other_branch_is_touched(self):
        sh(self.repo, "git", "branch", "other")
        other = sh(self.repo, "git", "rev-parse", "other")
        self.start()
        self.code.write("babyai/bye.py", "x = 1\n")
        self.code.test()
        self.code.apply()
        self.assertEqual(sh(self.repo, "git", "rev-parse", "other"), other)
        self.assertEqual(sh(self.repo, "git", "remote"), "")

    def test_it_will_not_run_outside_a_git_checkout(self):
        for repo in (None, self.root / "not-a-repo"):
            code = CodeUpgrades(repo, self.state)
            with self.assertRaisesRegex(UpgradeError, "git checkout"):
                code.start("anything")

    def test_previews_say_what_the_user_is_being_asked_to_allow(self):
        self.start("add a farewell")
        self.code.write("babyai/bye.py", "x = 1\n")
        self.assertEqual(self.code.preview_test()["run the tests on"], self.code.journal.load()["proposal"]["branch"])
        shown = self.code.preview_apply()
        self.assertEqual(shown["change"], "add a farewell")
        self.assertIn("babyai/bye.py", shown["files"])
        self.assertIn("NOT passed", shown["tests"])
        self.code.test()
        self.assertEqual(self.code.preview_apply()["tests"], "passed on this version")


# --------------------------------------------------------------------------- updates, rollback, restart


class UpdateTests(Workspace):
    def setUp(self):
        super().setUp()
        self.origin = self.root / "origin.git"
        sh(self.root, "git", "init", "-q", "--bare", "-b", "main", str(self.origin))
        sh(self.repo, "git", "remote", "add", "origin", str(self.origin))
        sh(self.repo, "git", "push", "-q", "-u", "origin", "main")
        self.mine = VersionUpdates(self.repo, self.state, UNITTEST)
        self.upstream = self.root / "upstream-work"  # somebody else's clone, to push new commits from
        sh(self.root, "git", "clone", "-q", str(self.origin), str(self.upstream))

    def publish(self, files, message="new version"):
        for name, text in files.items():
            (self.upstream / name).parent.mkdir(parents=True, exist_ok=True)
            (self.upstream / name).write_text(text)
        commit_all(self.upstream, message)
        sh(self.upstream, "git", "push", "-q", "origin", "main")

    def head(self):
        return sh(self.repo, "git", "rev-parse", "HEAD")

    def test_up_to_date(self):
        self.assertIn("up to date", self.mine.check())
        self.assertIn("Already up to date", self.mine.update())

    def test_check_lists_what_is_new(self):
        self.publish({"babyai/new.py": "x = 1\n"}, "add new.py")
        text = self.mine.check()
        self.assertIn("1 new commit", text)
        self.assertIn("add new.py", text)
        self.assertNotEqual(self.head(), sh(self.upstream, "git", "rev-parse", "HEAD"))  # checking changes nothing

    def test_update_tests_the_new_version_then_fast_forwards(self):
        before = self.head()
        self.publish({"babyai/new.py": "x = 1\n"}, "add new.py")
        self.assertIn("Updated", self.mine.update())
        self.assertEqual(self.head(), sh(self.upstream, "git", "rev-parse", "HEAD"))
        self.assertTrue((self.repo / "babyai" / "new.py").exists())
        entry = self.mine.journal.load()["applied"][-1]
        self.assertEqual((entry["kind"], entry["before"], entry["after"]), ("update", before, self.head()))
        self.assertEqual(sh(self.repo, "git", "worktree", "list").count("\n"), 0)  # the spare folder is gone

    def test_a_new_version_whose_tests_fail_is_not_pulled(self):
        before = self.head()
        self.publish({"babyai/hello.py": "def hello():\n    return 'broken'\n"}, "break it")
        with self.assertRaisesRegex(UpgradeError, "did not update"):
            self.mine.update()
        self.assertEqual(self.head(), before)
        self.assertEqual(sh(self.repo, "git", "worktree", "list").count("\n"), 0)

    def test_uncommitted_changes_block_an_update(self):
        self.publish({"babyai/new.py": "x = 1\n"})
        (self.repo / "babyai" / "hello.py").write_text("def hello():\n    return 'mine'\n")
        with self.assertRaisesRegex(UpgradeError, "uncommitted"):
            self.mine.update()

    def test_changes_of_his_own_that_the_remote_lacks_block_an_update(self):
        (self.repo / "mine.txt").write_text("local only\n")
        commit_all(self.repo, "a local commit")
        self.publish({"babyai/new.py": "x = 1\n"})
        with self.assertRaisesRegex(UpgradeError, "changes? of my own"):
            self.mine.update()
        self.assertIn("of my own", self.mine.check())

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

    def test_rollback_refuses_if_the_checkout_has_moved_since(self):
        self.publish({"babyai/new.py": "x = 1\n"})
        self.mine.update()
        (self.repo / "later.txt").write_text("work after the update\n")
        commit_all(self.repo, "later work")
        with self.assertRaisesRegex(UpgradeError, "git reset --hard"):
            self.mine.rollback()
        self.assertTrue((self.repo / "later.txt").exists())

    def test_rollback_refuses_with_uncommitted_changes(self):
        self.publish({"babyai/new.py": "x = 1\n"})
        self.mine.update()
        (self.repo / "babyai" / "hello.py").write_text("def hello():\n    return 'mine'\n")
        with self.assertRaisesRegex(UpgradeError, "uncommitted"):
            self.mine.rollback()

    def test_restart_runs_the_same_command_after_a_moment(self):
        done = threading.Event()
        said = self.mine.restart(delay=0.05, exec_fn=done.set)
        self.assertIn("Restarting", said)
        self.assertTrue(done.wait(2))


# --------------------------------------------------------------------------- his brain


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
    """Just enough of BabyAI for ModelUpgrades."""

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


# --------------------------------------------------------------------------- notes and skills


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


# --------------------------------------------------------------------------- the safety gate and the agent


class GateTests(unittest.TestCase):
    def test_what_changes_him_is_always_confirmed_at_every_safety_level(self):
        for level in ("strict", "moderate", "relaxed", "anything"):
            gate = SafetyGate(level)
            for tool, actions in UPGRADE_ACTION_RISK.items():
                for action, risk in actions.items():
                    verdict = gate.check(tool, {"action": action}, interactive=True)
                    self.assertEqual(verdict["needs_confirmation"], risk != RiskLevel.LOW, (level, tool, action))
                    self.assertEqual(verdict["allowed"], risk == RiskLevel.LOW, (level, tool, action))

    def test_an_action_nobody_has_rated_is_high_risk_and_confirmed(self):
        for args in ({"action": "format_disk"}, {}, {"action": ""}):
            self.assertEqual(get_risk_level("upgrade_code", args), RiskLevel.HIGH)
            self.assertTrue(SafetyGate("relaxed").check("upgrade_code", args)["needs_confirmation"])

    def test_looking_and_working_on_a_branch_are_free(self):
        for tool, action in (("upgrade_code", "start"), ("upgrade_code", "write"), ("upgrade_code", "diff"), ("upgrade_version", "check"),
                             ("upgrade_model", "list"), ("upgrade_notes", "show")):
            self.assertEqual(get_risk_level(tool, {"action": action}), RiskLevel.LOW, (tool, action))

    def test_making_things_live_is_not_free(self):
        for tool, action in (("upgrade_code", "test"), ("upgrade_code", "apply"), ("upgrade_version", "update"), ("upgrade_version", "rollback"),
                             ("upgrade_version", "restart"), ("upgrade_model", "pull"), ("upgrade_model", "switch")):
            self.assertEqual(get_risk_level(tool, {"action": action}), RiskLevel.HIGH, (tool, action))
        for action in ("add_note", "remove_note", "save_skill", "delete_skill"):
            self.assertEqual(get_risk_level("upgrade_notes", {"action": action}), RiskLevel.MEDIUM, action)

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


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.config = str(self.root / "config.toml")
        self.write_config()
        self.ai = BabyAI(config_path=self.config)

    def write_config(self, level="strict", extra=""):
        Path(self.config).write_text(
            f'[memory]\ndb_path = "{self.root}/mem.db"\n[safety]\nlevel = "{level}"\n[upgrade]\ndir = "{self.root}/self"\n{extra}'
        )

    def tearDown(self):
        self.dir.cleanup()

    def note(self, text="Call the user boss."):
        return call_tool("upgrade_notes", {"action": "add_note", "text": text})

    def test_the_four_tools_exist_and_can_be_switched_off(self):
        self.assertTrue({"upgrade_code", "upgrade_version", "upgrade_model", "upgrade_notes"} <= set(self.ai.tool_registry.tools))
        self.write_config(extra="enabled = false\n")
        off = BabyAI(config_path=self.config)
        self.assertIsNone(off.upgrade)
        self.assertFalse([name for name in off.tool_registry.tools if name.startswith("upgrade_")])
        self.assertEqual(off.context.build_messages()[0]["content"], off.context.system_prompt)

    def test_the_default_registry_has_none_of_them(self):
        self.assertFalse([name for name in build_default_registry().tools if name.startswith("upgrade_")])

    def test_the_user_is_shown_what_a_note_would_say_and_the_note_waits_for_their_yes(self):
        asked = []
        self.ai.confirm = lambda name, args, verdict: asked.append((name, args, verdict["risk_level"])) or False
        self.ai.llm_client = ScriptedLLM(self.note("Call the user boss."), say("They said no."))
        self.ai.process_user_input("call me boss")
        self.assertEqual(asked[0][0], "upgrade_notes")
        self.assertEqual(asked[0][1], {"add this note": "Call the user boss."})  # the tool's own summary, not the raw arguments
        self.assertEqual(self.ai.upgrade.notes.notes(), [])
        self.assertIn("declined", self.ai.llm_client.calls[1]["messages"][-1]["content"])

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

    def test_without_anyone_to_ask_it_is_declined_even_at_the_most_relaxed_level(self):
        self.write_config(level="relaxed")
        ai = BabyAI(config_path=self.config)
        self.assertIsNone(ai.confirm)
        ai.llm_client = ScriptedLLM(self.note(), say("Okay."))
        ai.process_user_input("call me boss")
        self.assertEqual(ai.upgrade.notes.notes(), [])

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

    def test_the_rounds_are_capped(self):
        self.ai.confirm = lambda *args: True
        calls = [call_tool("upgrade_notes", {"action": "show"}, f"c{n}") for n in range(MAX_ROUNDS + 3)]
        self.ai.llm_client = ScriptedLLM(*calls)
        reply = self.ai.process_user_input("keep going")
        calls_made = self.ai.llm_client.calls
        self.assertEqual(len(calls_made), 1 + MAX_ROUNDS)  # the first answer, then at most MAX_ROUNDS follow-ups
        self.assertIsNone(calls_made[-1]["tools"])  # the last one must talk, not call
        self.assertTrue(reply.startswith("upgrade_notes:"))  # she never gets to talk: show what the tools said
        self.assertNotIn("function", reply)

    def test_the_tool_calls_in_one_message_are_capped_too(self):
        self.ai.confirm = lambda *args: True

        def several(round_number, how_many):
            return {"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
                {"id": f"r{round_number}c{n}", "type": "function", "function": {"name": "upgrade_notes", "arguments": json.dumps({"action": "show"})}}
                for n in range(how_many)
            ]}}]}

        # 4 + 4 + 3 = 11 calls, one short of the cap, then a round of 4 that would cross it: only 1 of those may run
        sizes = [MAX_TOOL_CALLS, MAX_TOOL_CALLS, MAX_TOOL_CALLS - 1] + [MAX_TOOL_CALLS] * (MAX_ROUNDS + 2)
        self.assertEqual(sum(sizes[:3]), MAX_TOOL_CALLS_TOTAL - 1)
        self.ai.llm_client = ScriptedLLM(*[several(n, how_many) for n, how_many in enumerate(sizes)])
        self.ai.process_user_input("keep going")
        last = self.ai.llm_client.calls[-1]
        self.assertEqual(len([m for m in last["messages"] if m["role"] == "tool"]), MAX_TOOL_CALLS_TOTAL)
        self.assertIsNone(last["tools"])

    def test_a_whole_self_upgrade_through_the_agent_ends_with_the_change_live(self):
        repo = make_repo(self.root / "work")
        self.ai.upgrade = UpgradeContext(self.ai, self.ai.config, self.config, repo=repo)
        self.ai.upgrade.code.test_command = UNITTEST
        self.ai.tool_registry = build_default_registry(self.ai.memory, self.ai.config, upgrade=self.ai.upgrade)
        asked = []
        self.ai.confirm = lambda name, args, verdict: asked.append((name, dict(args))) or True
        self.ai.llm_client = ScriptedLLM(
            call_tool("upgrade_code", {"action": "start", "goal": "add a farewell"}, "1"),
            call_tool("upgrade_code", {"action": "write", "path": "babyai/bye.py", "content": "def bye():\n    return 'bye'\n"}, "2"),
            call_tool("upgrade_code", {"action": "test"}, "3"),
            call_tool("upgrade_code", {"action": "diff"}, "4"),
            call_tool("upgrade_code", {"action": "apply"}, "5"),
            say("Done: I can say bye now. Restart me to use it."),
        )
        reply = self.ai.process_user_input("teach yourself to say bye")
        self.assertEqual(reply, "Done: I can say bye now. Restart me to use it.")
        self.assertTrue((repo / "babyai" / "bye.py").exists())
        self.assertEqual([name for name, _ in asked], ["upgrade_code", "upgrade_code"])  # only the test run and the apply asked
        self.assertIn("run the tests on", asked[0][1])
        self.assertEqual(asked[1][1]["tests"], "passed on this version")


if __name__ == "__main__":
    unittest.main()
