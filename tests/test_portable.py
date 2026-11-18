"""Portable paths, durable writes, the SQLite settings and the trust manifest."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from babyai import atomic, portable
from babyai.config.loader import load_config
from babyai.memory.db import MemoryStore


@contextmanager
def clean_environment():
    keys = ("BABYAI_HOME", "BABYAI_PAIRING", "XDG_CACHE_HOME")
    saved = {key: os.environ.pop(key, None) for key in keys}
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def build_stick_tree(base: Path) -> Path:
    """A BabyAI folder as it is on a stick, with everything the manifest has to treat differently."""
    root = base / "BabyAI"
    files = {
        "babyai/__init__.py": "", "babyai/a.py": "A = 1\n", "babyai/runtime/inner.py": "RUN = 1\n",  # (a package that is only called runtime)
        "babyai/__pycache__/stale.py": "x = 1\n", "tests/test_a.py": "", "config.toml": "[llm]\n", "notes.txt": "not code\n",
        "data/memory.db": "", "data/self/worktrees/w/x.py": "his own scratch\n",
        "runtime/bin/python3": "bundled python", "runtime/lib/site.py": "SITE = 1\n",
        "ollama/bin/ollama": "bundled ollama", "ollama/models/blob": "weights",
        ".git/config": "[core]\n\tfileMode = false\n", ".git/info/attributes": "", ".git/hooks/post-merge": "#!/bin/sh\n",
        ".git/hooks/pre-commit.sample": "#!/bin/sh\n", ".git/objects/aa/bb": "object",
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


class PortablePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "babyai").mkdir()
        (self.root / "babyai" / "__init__.py").write_text("")
        self.config = self.root / "config.toml"

    def test_a_portable_config_next_to_the_app_moves_every_data_path(self) -> None:
        self.config.write_text('[usb]\nmode = "portable"\n')
        with clean_environment():
            loaded = load_config(str(self.config))
        data = self.root / "data"
        self.assertEqual(loaded["memory"]["db_path"], str(data / "memory.db"))
        self.assertEqual(loaded["upgrade"]["dir"], str(data / "self"))
        self.assertEqual(loaded["habits"]["dir"], str(data / "habits"))
        self.assertEqual(loaded["personality"]["dir"], str(data / "personality"))
        self.assertEqual(loaded["usb"]["mode"], "portable")

    def test_babyai_home_wins_even_without_a_config_file(self) -> None:
        with clean_environment(), mock.patch.dict(os.environ, {"BABYAI_HOME": str(self.root)}):
            loaded = load_config(str(self.root / "missing.toml"))
        self.assertEqual(loaded["memory"]["db_path"], str(self.root / "data" / "memory.db"))

    def test_key_mode_keeps_the_old_home_paths(self) -> None:
        self.config.write_text('[usb]\nmode = "key"\n')
        with clean_environment():
            loaded = load_config(str(self.config))
        self.assertNotIn(str(self.root / "data"), loaded["memory"]["db_path"])
        self.assertIn(".local/share/babyai", loaded["memory"]["db_path"])

    def test_prepare_environment_creates_folders_and_never_changes_home(self) -> None:
        home = os.environ.get("HOME")
        with clean_environment():
            root = portable.prepare_environment(self.root)
            self.assertEqual(os.environ["BABYAI_HOME"], str(self.root))
            self.assertEqual(os.environ["XDG_CACHE_HOME"], str(self.root / "data" / "cache"))
            self.assertEqual(os.environ.get("HOME"), home)
        for name in ("", "cache", "habits", "personality", "reminders", "self"):
            self.assertTrue((root / "data" / name).is_dir(), name)

    def test_pairing_file_round_trips_through_the_small_toml_writer(self) -> None:
        path = self.root / "pairing.toml"
        data = {"mode": "portable", "drive": "uuid:ABCD", "grace_seconds": 4, "trust": {"babyai/a.py": "abc"}}
        portable.save_pairing(data, path)
        self.assertEqual(portable.load_pairing(path), data)


class AtomicWriteTests(unittest.TestCase):
    def test_it_writes_to_a_temporary_then_renames_and_fsyncs(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch("babyai.atomic.os.fsync") as fsync:
            path = Path(directory) / "state.json"
            atomic.write_text(path, '{"ok": true}')
            self.assertEqual(path.read_text(), '{"ok": true}')
            self.assertFalse(path.with_name("state.json.tmp").exists())
            self.assertGreaterEqual(fsync.call_count, 1)

    def test_a_failed_write_leaves_the_old_file_alone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("old")
            with mock.patch("babyai.atomic.os.replace", side_effect=OSError("read-only")):
                with self.assertRaises(OSError):
                    atomic.write_text(path, "new")
            self.assertEqual(path.read_text(), "old")


class MemoryDurabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "memory.db"

    def test_it_uses_delete_journal_full_sync_and_leaves_no_wal(self) -> None:
        store = MemoryStore(str(self.db))
        self.addCleanup(store.close)
        store.remember_fact("The user likes ramen.", "test")
        mode = store.connection.execute("PRAGMA journal_mode").fetchone()[0]
        sync = store.connection.execute("PRAGMA synchronous").fetchone()[0]
        self.assertEqual(str(mode).lower(), "delete")
        self.assertEqual(int(sync), 2)  # FULL
        self.assertFalse(self.db.with_name("memory.db-wal").exists())

    def test_a_fact_survives_a_close_and_reopen(self) -> None:
        store = MemoryStore(str(self.db))
        store.remember_fact("The user has a cat called Miso.", "test")
        store.close()
        again = MemoryStore(str(self.db))
        self.addCleanup(again.close)
        self.assertTrue(any("Miso" in row["fact"] for row in again.recent_facts()))

    def test_a_corrupt_database_is_refused_instead_of_overwritten(self) -> None:
        self.db.write_bytes(b"this is not a sqlite database")
        with self.assertRaises(sqlite3.DatabaseError):
            MemoryStore(str(self.db))

    def test_a_disk_failure_is_reported_without_losing_the_answer(self) -> None:
        store = MemoryStore(str(self.db))
        self.addCleanup(store.close)
        broken = mock.Mock()
        broken.execute.side_effect = sqlite3.OperationalError("disk I/O error")
        store._local.connection = broken
        result = store.remember_fact("a fact", "test")
        self.assertFalse(result["saved"])
        self.assertIn("remember_fact", store.last_write_error or "")


class TrustTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "babyai").mkdir()
        (self.root / "babyai" / "a.py").write_text("A = 1\n")
        (self.root / "babyai" / "b.py").write_text("B = 2\n")
        (self.root / "README.md").write_text("not python")
        self.pairing = self.root / "pairing.toml"
        self.data = {"mode": "portable", "drive": "uuid:ABCD", "app_folder": "BabyAI", "trust": portable.manifest(self.root)}
        portable.save_pairing(self.data, self.pairing)

    def test_it_lists_only_python_files_that_match_or_not(self) -> None:
        self.assertEqual(portable.trust_changes(self.data, self.root), [])
        (self.root / "babyai" / "a.py").write_text("A = 99\n")
        self.assertEqual(portable.trust_changes(self.data, self.root), ["babyai/a.py"])

    def test_refresh_updates_changed_files_and_forgets_deleted_ones(self) -> None:
        (self.root / "babyai" / "a.py").write_text("A = 99\n")
        (self.root / "babyai" / "b.py").unlink()
        with clean_environment(), mock.patch.dict(os.environ, {"BABYAI_HOME": str(self.root), "BABYAI_PAIRING": str(self.pairing)}):
            self.assertTrue(portable.refresh_trust(["babyai/a.py", "babyai/b.py", "README.md"]))
        saved = portable.load_pairing(self.pairing)
        self.assertEqual(saved["trust"]["babyai/a.py"], portable.sha256(self.root / "babyai" / "a.py"))
        self.assertNotIn("babyai/b.py", saved["trust"])
        self.assertNotIn("README.md", saved["trust"])

    def test_refresh_does_nothing_outside_portable_mode(self) -> None:
        key = {"mode": "key", "trust": {}}
        portable.save_pairing(key, self.pairing)
        with clean_environment(), mock.patch.dict(os.environ, {"BABYAI_HOME": str(self.root), "BABYAI_PAIRING": str(self.pairing)}):
            self.assertFalse(portable.refresh_trust(["babyai/a.py"]))
        self.assertEqual(portable.load_pairing(self.pairing), key)


class EnvironmentRootTests(unittest.TestCase):
    """BABYAI_HOME is for the app's own config.toml. Everything else keeps the paths it was given."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "stick" / "BabyAI"
        (self.root / "babyai").mkdir(parents=True)
        (self.root / "babyai" / "__init__.py").write_text("")
        self.other = Path(self._tmp.name) / "elsewhere"
        self.other.mkdir()

    def test_the_variable_belongs_to_the_config_in_that_folder(self) -> None:
        with clean_environment(), mock.patch.dict(os.environ, {"BABYAI_HOME": str(self.root)}):
            self.assertEqual(portable.root_for({}, self.root / "config.toml"), self.root.resolve())
            self.assertIsNone(portable.root_for({}, self.other / "config.toml"))

    def test_another_config_keeps_its_own_paths_with_the_variable_set(self) -> None:
        elsewhere = self.other / "config.toml"
        elsewhere.write_text(f'[memory]\ndb_path = "{self.other / "mine.db"}"\n')
        with clean_environment(), mock.patch.dict(os.environ, {"BABYAI_HOME": str(self.root)}):
            loaded = load_config(str(elsewhere))
        self.assertEqual(loaded["memory"]["db_path"], str(self.other / "mine.db"))
        self.assertNotEqual(loaded["usb"]["mode"], "portable")

    def test_the_tests_he_runs_for_a_self_upgrade_cannot_see_the_portable_setting(self) -> None:
        from babyai.upgrade.gitops import run_tests

        project = self.other / "project"
        project.mkdir()
        (project / "test_env.py").write_text(
            "import os, unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_clean(self):\n"
            "        self.assertFalse(os.environ.get('BABYAI_HOME'))\n"
            "        self.assertFalse(os.environ.get('BABYAI_PAIRING'))\n"
        )
        with mock.patch.dict(os.environ, {"BABYAI_HOME": str(self.root), "BABYAI_PAIRING": str(self.other / "pairing.toml")}):
            passed, summary = run_tests(project, [sys.executable, "-m", "unittest", "test_env"])
        self.assertTrue(passed, summary)

    def test_a_whole_test_suite_run_with_the_variable_set_leaves_the_stick_alone(self) -> None:
        """The leak this guards against: every test's own config was pointed at the real data on the stick."""
        project = self.other / "project"
        project.mkdir()
        repo = Path(__file__).resolve().parents[1]
        (project / "test_leak.py").write_text(
            "import tempfile, unittest\n"
            "from pathlib import Path\n"
            "from babyai.agent.core import BabyAI\n"
            "class T(unittest.TestCase):\n"
            "    def test_own_paths(self):\n"
            "        with tempfile.TemporaryDirectory() as tmp:\n"
            "            config = Path(tmp) / 'config.toml'\n"
            "            config.write_text(f'[memory]\\ndb_path = \"{tmp}/own.db\"\\n')\n"
            "            assistant = BabyAI(config_path=str(config))\n"
            "            assistant.memory.remember_fact('a test fact')\n"
            "            assistant.memory.close()\n"
            "            self.assertTrue((Path(tmp) / 'own.db').exists())\n"
        )
        env = {**os.environ, "BABYAI_HOME": str(self.root), "PYTHONPATH": str(repo)}
        done = __import__("subprocess").run(
            [sys.executable, "-m", "unittest", "test_leak"], cwd=project, env=env, capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse((self.root / "data" / "memory.db").exists(), "the test wrote into the stick's memory")


class RefreshScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.root = build_stick_tree(base / "stick")
        self.other = base / "scratch-copy"
        self.other.mkdir()
        self.pairing = base / "cfg" / "pairing.toml"
        portable.save_pairing({"mode": "portable", "drive": "uuid:ABCD-1234", "trust": portable.manifest(self.root)}, self.pairing)
        patch = mock.patch.dict(os.environ, {"BABYAI_HOME": str(self.root), "BABYAI_PAIRING": str(self.pairing)})
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_change_in_the_portable_folder_updates_the_manifest(self) -> None:
        (self.root / "babyai" / "a.py").write_text("A = 2\n")
        self.assertTrue(portable.refresh_trust(["babyai/a.py"], self.root))
        self.assertEqual(portable.load_pairing(self.pairing)["trust"]["babyai/a.py"], portable.sha256(self.root / "babyai" / "a.py"))

    def test_a_change_in_any_other_repository_is_ignored(self) -> None:
        before = self.pairing.read_bytes()
        (self.root / "babyai" / "a.py").write_text("A = 2\n")
        self.assertFalse(portable.refresh_trust(["babyai/a.py"], self.other))
        self.assertEqual(self.pairing.read_bytes(), before)

    def test_names_are_cleaned_up_and_never_turned_into_other_files(self) -> None:
        (self.root / ".hidden").mkdir()
        (self.root / ".hidden" / "x.py").write_text("X = 1\n")
        (self.root / "babyai" / "a.py").write_text("A = 3\n")
        names = ["./babyai/a.py", ".hidden/x.py", "../outside.py", "/etc/passwd.py", "babyai/../../escape.py", "notes.txt", ""]
        self.assertTrue(portable.refresh_trust(names, self.root))
        trust = portable.load_pairing(self.pairing)["trust"]
        self.assertEqual(trust["babyai/a.py"], portable.sha256(self.root / "babyai" / "a.py"))
        self.assertEqual(trust[".hidden/x.py"], portable.sha256(self.root / ".hidden" / "x.py"))
        self.assertNotIn("hidden/x.py", trust)  # (a leading dot is part of the name)
        for bad in ("../outside.py", "outside.py", "/etc/passwd.py", "escape.py", "../escape.py", "notes.txt"):
            self.assertNotIn(bad, trust)


class ManifestCoverageTests(unittest.TestCase):
    """Everything on the stick that can run as him is in the manifest. His own data is not."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = build_stick_tree(Path(self._tmp.name))

    def test_what_can_run_is_covered(self) -> None:
        found = portable.manifest(self.root)
        for name in ("babyai/__init__.py", "babyai/a.py", "babyai/runtime/inner.py", "tests/test_a.py", ".git/config",
                     ".git/info/attributes", ".git/hooks/post-merge", "runtime/", "ollama/bin/"):
            self.assertIn(name, found)

    def test_his_data_samples_caches_and_models_are_not(self) -> None:
        found = portable.manifest(self.root)
        for name in ("data/self/worktrees/w/x.py", ".git/hooks/pre-commit.sample", "babyai/__pycache__/stale.py", "ollama/models/blob",
                     "runtime/lib/site.py", "notes.txt", "config.toml", ".git/objects/aa/bb", "data/memory.db"):
            self.assertNotIn(name, found)

    def test_swapping_a_bundled_program_changes_its_digest(self) -> None:
        before = portable.manifest(self.root)
        (self.root / "runtime" / "bin" / "python3").write_text("swapped")
        (self.root / "ollama" / "bin" / "ollama").write_text("swapped")
        after = portable.manifest(self.root)
        self.assertNotEqual(before["runtime/"], after["runtime/"])
        self.assertNotEqual(before["ollama/bin/"], after["ollama/bin/"])

    def test_a_python_file_inside_a_bundle_changes_the_bundles_digest(self) -> None:
        before = portable.manifest(self.root)["runtime/"]
        (self.root / "runtime" / "lib" / "site.py").write_text("SITE = 'planted'\n")
        self.assertNotEqual(portable.manifest(self.root)["runtime/"], before)

    def test_downloading_a_model_or_using_him_does_not_change_the_manifest(self) -> None:
        before = portable.manifest(self.root)
        (self.root / "ollama" / "models" / "blob").write_text("a new model")
        (self.root / "data" / "memory.db").write_text("more memories")
        (self.root / "data" / "self" / "worktrees" / "w" / "x.py").write_text("changed scratch")
        self.assertEqual(portable.manifest(self.root), before)

    def test_a_planted_hook_or_git_setting_is_noticed(self) -> None:
        trusted = {"trust": portable.manifest(self.root)}
        (self.root / ".git" / "hooks" / "post-checkout").write_text("#!/bin/sh\nevil\n")
        (self.root / ".git" / "config").write_text("[core]\n\tfsmonitor = /tmp/evil\n")
        self.assertEqual(portable.trust_changes(trusted, self.root), [".git/config", ".git/hooks/post-checkout"])


if __name__ == "__main__":
    unittest.main()
