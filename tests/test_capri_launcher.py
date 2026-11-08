import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from babyai import capri_launcher
from babyai.capri_launcher import ENV_VAR, launch_capri

POSIX = os.name != "nt"


def _fake_binary(directory: Path, body: str) -> Path:
    path = directory / capri_launcher.EXE_NAME
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class FindCapriTests(unittest.TestCase):
    def test_env_var_file_and_folder_are_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / capri_launcher.EXE_NAME
            binary.write_text("x")
            for value in (str(binary), tmp):
                with mock.patch.dict(os.environ, {ENV_VAR: value}):
                    self.assertEqual(capri_launcher.find_capri(), binary)

    def test_env_var_pointing_nowhere_does_not_fall_back(self):
        with mock.patch.dict(os.environ, {ENV_VAR: "/nonexistent/Capri"}):
            self.assertIsNone(capri_launcher.find_capri())
            result = launch_capri()
        self.assertFalse(result.ok)
        self.assertIn(ENV_VAR, result.message)

    def test_unbuilt_capri_gives_build_commands(self):
        with mock.patch.dict(os.environ, {ENV_VAR: ""}), mock.patch.object(capri_launcher, "CAPRI_DIR", Path("/nonexistent/capri")):
            result = launch_capri()
        self.assertFalse(result.ok)
        self.assertIn("dotnet build cross/Capri.Cross.csproj -c Release -o out/cross", result.message)
        self.assertIn("build-windows.ps1", result.message)


@unittest.skipUnless(POSIX, "uses shell-script stand-ins for the Capri binary")
class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        patcher = mock.patch.dict(os.environ, {"DISPLAY": ":0"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_running_binary_counts_as_started(self):
        binary = _fake_binary(self.dir, "sleep 30")
        result = launch_capri(binary, settle_seconds=0.3)
        try:
            self.assertTrue(result.ok, result.message)
            self.assertIsNotNone(result.process)
            self.assertIn(str(result.process.pid), result.message)
        finally:
            if result.process:
                result.process.terminate()
                result.process.wait(timeout=5)

    def test_immediate_exit_is_reported_with_code(self):
        binary = _fake_binary(self.dir, "exit 3")
        result = launch_capri(binary, settle_seconds=5)
        self.assertFalse(result.ok)
        self.assertIn("code 3", result.message)

    def test_non_executable_file_is_reported(self):
        binary = _fake_binary(self.dir, "sleep 30")
        binary.chmod(0o644)
        result = launch_capri(binary, settle_seconds=0.3)
        self.assertFalse(result.ok)
        self.assertIn("chmod +x", result.message)

    @unittest.skipUnless(sys.platform.startswith("linux"), "display check is Linux-only")
    def test_headless_linux_is_reported_without_launching(self):
        binary = _fake_binary(self.dir, "sleep 30")
        env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("subprocess.Popen") as popen:
            result = launch_capri(binary, settle_seconds=0.3)
        self.assertFalse(result.ok)
        self.assertIn("graphical desktop", result.message)
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
