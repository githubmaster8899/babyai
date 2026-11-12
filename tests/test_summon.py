"""Showing and hiding the running overlay from outside (babyai/ui/summon.py, and the --toggle / --show / --hide flags). Display-free."""

from __future__ import annotations

import os
import queue
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

if sys.platform == "win32":
    raise unittest.SkipTest("unix sockets only")

from babyai import __main__ as cli  # noqa: E402
from babyai.config.loader import DEFAULT_CONFIG  # noqa: E402
from babyai.ui import summon  # noqa: E402
from babyai.ui.summon import SummonServer, send, socket_path  # noqa: E402


class Folder(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(dir="/tmp")  # (a short path: socket paths have a length limit)
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "s.sock"
        self.commands: queue.Queue[str] = queue.Queue()

    def serve(self, path: Path | None = None) -> SummonServer:
        server = SummonServer(path or self.path, self.commands)
        self.assertTrue(server.start())
        self.addCleanup(server.stop)
        return server

    def got(self, timeout: float = 2.0) -> str | None:
        try:
            return self.commands.get(timeout=timeout)
        except queue.Empty:
            return None


class PathTests(Folder):
    def test_it_lives_in_the_runtime_folder(self) -> None:
        self.assertEqual(socket_path({"XDG_RUNTIME_DIR": str(self.dir)}), self.dir / "babyai-overlay.sock")

    def test_without_a_runtime_folder_it_uses_a_folder_of_ones_own_in_tmp(self) -> None:
        for env in ({}, {"XDG_RUNTIME_DIR": str(self.dir / "missing")}):
            with self.subTest(env=env):
                path = socket_path(env)
                self.assertEqual(path.parent.name, f"babyai-{os.getuid()}")
                self.assertEqual(path.parent.parent, Path(tempfile.gettempdir()))

    def test_the_default_config_turns_it_on(self) -> None:
        self.assertIs(DEFAULT_CONFIG["ui"]["summon"], True)


class ServerTests(Folder):
    def test_each_word_arrives_and_is_answered(self) -> None:
        self.serve()
        for word in summon.COMMANDS:
            with self.subTest(word=word):
                self.assertEqual(send(word, self.path), "ok")
                self.assertEqual(self.got(), word)

    def test_case_and_the_newline_do_not_matter(self) -> None:
        self.serve()
        self.assertEqual(send("TOGGLE", self.path), "ok")
        self.assertEqual(self.got(), "toggle")

    def test_anything_else_is_refused_and_nothing_reaches_the_overlay(self) -> None:
        self.serve()
        for word in ("", "quit", "toggle; rm -rf ~", "../x", "close", "toggle toggle", "x" * 500):
            with self.subTest(word=word[:20]):
                self.assertEqual(send(word, self.path), "unknown")
        self.assertIsNone(self.got(0.1))

    def test_a_client_that_says_nothing_does_not_hold_the_others_up(self) -> None:
        self.serve()
        quiet = socket.socket(socket.AF_UNIX)
        quiet.connect(str(self.path))
        self.addCleanup(quiet.close)
        started = time.monotonic()
        self.assertEqual(send("show", self.path, timeout=5), "ok")
        self.assertLess(time.monotonic() - started, 3)

    def test_many_at_once(self) -> None:
        self.serve()
        results: list[str | None] = []
        threads = [threading.Thread(target=lambda: results.append(send("show", self.path))) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(results, ["ok"] * 8)
        self.assertEqual(self.commands.qsize(), 8)

    def test_only_this_user_can_reach_it(self) -> None:
        self.serve()
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_a_folder_it_has_to_make_is_private(self) -> None:
        nested = self.dir / "babyai-private" / "overlay.sock"
        self.serve(nested)
        self.assertEqual(stat.S_IMODE(os.stat(nested.parent).st_mode), 0o700)

    def test_nobody_listening_is_none_not_an_error(self) -> None:
        self.assertIsNone(send("toggle", self.path))

    def test_a_leftover_socket_from_a_crashed_overlay_is_replaced(self) -> None:
        dead = socket.socket(socket.AF_UNIX)
        dead.bind(str(self.path))
        dead.close()  # the file stays behind, nobody listens
        self.assertTrue(self.path.exists())
        self.assertIsNone(send("toggle", self.path))
        self.serve()
        self.assertEqual(send("hide", self.path), "ok")

    def test_a_second_overlay_does_not_steal_it_from_the_first(self) -> None:
        self.serve()
        second = SummonServer(self.path, queue.Queue())
        self.assertFalse(second.start())
        self.assertEqual(send("show", self.path), "ok")
        self.assertEqual(self.got(), "show")

    def test_stopping_removes_the_socket_and_stops_answering(self) -> None:
        server = self.serve()
        server.stop()
        self.assertFalse(self.path.exists())
        self.assertIsNone(send("show", self.path))
        server.stop()  # (twice is fine)

    def test_when_there_is_no_place_for_it_it_says_so_instead_of_failing(self) -> None:
        blocker = self.dir / "file"
        blocker.write_text("")
        self.assertFalse(SummonServer(blocker / "inside" / "s.sock", self.commands).start())
        self.assertFalse(SummonServer(Path("/proc/nope/s.sock"), self.commands).start())

    def test_a_path_that_is_too_long_for_a_socket_says_so_instead_of_failing(self) -> None:
        self.assertFalse(SummonServer(self.dir / ("x" * 200) / "s.sock", self.commands).start())

    def test_a_dangling_link_in_its_place_does_not_stop_it_either(self) -> None:
        self.path.symlink_to(self.dir / "nowhere")
        self.serve()
        self.assertEqual(send("show", self.path), "ok")

    def test_a_leftover_link_in_its_place_is_removed_not_followed(self) -> None:
        target = self.dir / "precious"
        target.write_text("keep")
        self.path.symlink_to(target)
        self.serve()
        self.assertEqual(target.read_text(), "keep")
        self.assertFalse(self.path.is_symlink())


class CommandLineTests(Folder):
    def run_cli(self, *argv: str) -> tuple[int, str]:
        out = StringIO()
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.dir)}), redirect_stdout(out), \
                mock.patch.object(cli, "BabyAI", side_effect=AssertionError("must not start an assistant to send a word")):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_without_an_overlay_running_it_says_how_to_start_one(self) -> None:
        code, out = self.run_cli("--toggle")
        self.assertEqual(code, 1)
        self.assertIn("isn't running", out)
        self.assertIn("--desktop", out)

    def test_each_flag_sends_its_word(self) -> None:
        self.serve(self.dir / "babyai-overlay.sock")
        for flag in ("--toggle", "--show", "--hide"):
            with self.subTest(flag=flag):
                self.assertEqual(self.run_cli(flag), (0, ""))
                self.assertEqual(self.got(), flag[2:])

    def test_the_flags_are_alternatives(self) -> None:
        with self.assertRaises(SystemExit), redirect_stdout(StringIO()), mock.patch("sys.stderr", StringIO()):
            cli.main(["--toggle", "--hide"])

    def test_an_overlay_that_answers_something_else_is_reported(self) -> None:
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(self.dir / "babyai-overlay.sock"))
        listener.listen(1)
        self.addCleanup(listener.close)

        def reply() -> None:
            connection, _ = listener.accept()
            connection.recv(32)
            connection.sendall(b"unknown\n")
            connection.close()

        threading.Thread(target=reply, daemon=True).start()
        code, out = self.run_cli("--show")
        self.assertEqual(code, 1)
        self.assertIn("didn't understand", out)


if __name__ == "__main__":
    unittest.main()
