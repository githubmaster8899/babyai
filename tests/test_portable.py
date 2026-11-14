"""Portable paths, durable writes, the SQLite settings and the trust manifest."""

from __future__ import annotations

import json
import os
import sqlite3
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


if __name__ == "__main__":
    unittest.main()
