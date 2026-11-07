"""The guard rails around his self-upgrade, pinned by a test file he cannot edit.

He may change his own upgrade code (babyai/upgrade/, babyai/tools/upgrade.py), so what holds him back can't live only
there: this file is on the protected list, and the suite that must pass before any change goes live includes it. A change
that weakens one of these - dropping a protected path, letting a change go live without passing tests, skipping the check
that the checkout is clean or hasn't moved, rolling back over later work, skipping the user's yes - fails here, and
a change that fails tests is never applied. (Nothing here is a sandbox; see babyai/upgrade/code.py.)

The tests use real git repositories in temporary folders; a local "origin" stands in for GitHub."""


import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from babyai.agent.core import MAX_ROUNDS, MAX_TOOL_CALLS, MAX_TOOL_CALLS_TOTAL, BabyAI
from babyai.safety.gate import SafetyGate
from babyai.safety.rules import UPGRADE_ACTION_RISK, RiskLevel, get_risk_level
from babyai.tools.registry import build_default_registry
from babyai.upgrade import UpgradeContext
from babyai.upgrade.code import PROTECTED, CodeUpgrades, is_protected
from babyai.upgrade.gitops import UpgradeError
from babyai.upgrade.update import VersionUpdates
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


class CodeBase(Workspace):
    def setUp(self):
        super().setUp()
        self.code = CodeUpgrades(self.repo, self.state, UNITTEST)

    def start(self, goal="add a farewell"):
        self.code.start(goal)
        return Path(self.code.journal.load()["proposal"]["path"])

    def live_head(self):
        return sh(self.repo, "git", "rev-parse", "HEAD")


class UpdateBase(Workspace):
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


class AgentBase(unittest.TestCase):
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


class ProtectedPathTests(unittest.TestCase):
    # What he can never change through his tools, whatever else changes: the rules and the gate that make him ask,
    # the place the gate is enforced, the tests that pin all this, the licence notices, git's own folder.
    LOCKED = ("babyai/safety/", "babyai/agent/core.py", "tests/test_safety.py", "tests/test_guardrails.py", "NOTICE", "LICENSE", "capri/LICENSE.md", ".git/")

    def test_the_rules_the_gate_the_guard_rail_tests_and_the_licence_files_are_protected(self):
        for path in ("babyai/safety/gate.py", "babyai/safety/rules.py", "babyai/safety/anything_new.py", "babyai/agent/core.py", "NOTICE", "LICENSE",
                     "capri/LICENSE.md", "tests/test_safety.py", "tests/test_guardrails.py", ".git/config", "BABYAI/SAFETY/rules.py",
                     "Tests/Test_Guardrails.py", "sub/.git/hooks/pre-commit"):
            self.assertTrue(is_protected(path), path)

    def test_the_protected_list_can_grow_but_not_shrink(self):
        self.assertLessEqual(set(self.LOCKED), set(PROTECTED))

    def test_ordinary_files_are_not(self):
        for path in ("babyai/tools/clock.py", "babyai/ui/app.py", "tests/test_clock.py", "babyai/safetyish.py", "docs/NOTICE.md"):
            self.assertFalse(is_protected(path), path)

    def test_every_rule_is_something_that_exists_in_the_repo(self):
        root = Path(__file__).resolve().parents[1]
        for rule in PROTECTED:
            if rule == ".git/" or (rule == "LICENSE" and not (root / rule).exists()):
                continue  # .git is a rule, not a file of the repo; this checkout may not have a LICENSE
            self.assertTrue((root / rule.rstrip("/")).exists(), f"{rule} is listed as protected but is not in the repo")


class CodeGuardTests(CodeBase):
    def test_protected_files_cannot_be_written_or_edited(self):
        self.start()
        for path in ("babyai/safety/rules.py", "babyai/safety/new.py", "NOTICE", "capri/LICENSE.md", "babyai/agent/core.py",
                     "tests/test_safety.py", "tests/test_guardrails.py"):
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

    def test_uncommitted_work_in_the_live_checkout_blocks_apply(self):
        self.start()
        self.code.write("babyai/bye.py", "x = 1\n")
        self.code.test()
        (self.repo / "babyai" / "hello.py").write_text("def hello():\n    return 'edited by hand'\n")
        with self.assertRaisesRegex(UpgradeError, "uncommitted"):
            self.code.apply()
        self.assertEqual((self.repo / "babyai" / "hello.py").read_text(), "def hello():\n    return 'edited by hand'\n")

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

    def test_nothing_is_pushed_and_no_other_branch_is_touched(self):
        sh(self.repo, "git", "branch", "other")
        other = sh(self.repo, "git", "rev-parse", "other")
        self.start()
        self.code.write("babyai/bye.py", "x = 1\n")
        self.code.test()
        self.code.apply()
        self.assertEqual(sh(self.repo, "git", "rev-parse", "other"), other)
        self.assertEqual(sh(self.repo, "git", "remote"), "")

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
        self.assertEqual(shown["upgrade code"], "untouched")

    def test_a_change_to_his_own_upgrade_code_is_flagged_to_the_user_every_time_they_are_asked(self):
        folder = self.start("let me say hello in the upgrade tools")
        self.code.write("babyai/upgrade/helper.py", "x = 1\n")  # allowed now ...
        self.assertTrue((folder / "babyai" / "upgrade" / "helper.py").exists())
        self.assertEqual(self.code.preview_test()["also changes"], "my own upgrade code")  # ... but when they are asked to run the tests,
        self.assertIn("CHANGED", self.code.preview_apply()["upgrade code"])  # when they are asked to make it live,
        self.assertIn("edits my own upgrade code", self.code.diff())  # in the diff he reads out,
        self.code.test()
        before = sh(self.repo, "git", "rev-parse", "HEAD")
        said = self.code.apply()
        self.assertIn("changes my own upgrade code", said)  # and in what he is told afterwards
        self.assertIn(f"git -C {self.repo} reset --hard {before[:8]}", said)  # with the way back, whatever state he is in

    def test_the_diff_never_claims_passing_tests_that_have_not_passed(self):
        self.start()
        self.code.write("babyai/bye.py", "x = 1\n")
        self.assertIn("tests have NOT passed", self.code.diff())
        self.code.test()
        self.assertIn("tests passed on this version", self.code.diff())
        self.code.write("babyai/bye.py", "x = 2\n")  # a version nobody has tested
        self.assertIn("tests have NOT passed", self.code.diff())
        self.code.write("babyai/hello.py", "def hello():\n    return 'broken'\n")  # and one whose tests fail
        self.assertIn("FAILED", self.code.test())
        self.assertIn("tests have NOT passed", self.code.diff())

    def test_an_ordinary_change_carries_no_such_flag(self):
        self.start()
        self.code.write("babyai/bye.py", "x = 1\n")
        self.assertNotIn("also changes", self.code.preview_test())
        self.assertNotIn("upgrade code", self.code.diff())
        self.code.test()
        self.assertNotIn("changes my own upgrade code", self.code.apply())


class UpdateGuardTests(UpdateBase):
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


class GateGuardTests(unittest.TestCase):
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

    def test_making_things_live_is_not_free(self):
        for tool, action in (("upgrade_code", "test"), ("upgrade_code", "apply"), ("upgrade_version", "update"), ("upgrade_version", "rollback"),
                             ("upgrade_version", "restart"), ("upgrade_model", "pull"), ("upgrade_model", "switch")):
            self.assertEqual(get_risk_level(tool, {"action": action}), RiskLevel.HIGH, (tool, action))
        for action in ("add_note", "remove_note", "save_skill", "delete_skill"):
            self.assertEqual(get_risk_level("upgrade_notes", {"action": action}), RiskLevel.MEDIUM, action)


class AgentGuardTests(AgentBase):
    def test_the_user_is_shown_what_a_note_would_say_and_the_note_waits_for_their_yes(self):
        asked = []
        self.ai.confirm = lambda name, args, verdict: asked.append((name, args, verdict["risk_level"])) or False
        self.ai.llm_client = ScriptedLLM(self.note("Call the user boss."), say("They said no."))
        self.ai.process_user_input("call me boss")
        self.assertEqual(asked[0][0], "upgrade_notes")
        self.assertEqual(asked[0][1], {"add this note": "Call the user boss."})  # the tool's own summary, not the raw arguments
        self.assertEqual(self.ai.upgrade.notes.notes(), [])
        self.assertIn("declined", self.ai.llm_client.calls[1]["messages"][-1]["content"])

    def test_without_anyone_to_ask_it_is_declined_even_at_the_most_relaxed_level(self):
        self.write_config(level="relaxed")
        ai = BabyAI(config_path=self.config)
        self.assertIsNone(ai.confirm)
        ai.llm_client = ScriptedLLM(self.note(), say("Okay."))
        ai.process_user_input("call me boss")
        self.assertEqual(ai.upgrade.notes.notes(), [])

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
