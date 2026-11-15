"""The USB launcher (babyai/usbwatch.py): the drive is a key that starts him when it is plugged in and closes him when it is taken out.

There is no real USB drive here. /dev/disk is imitated with a folder of symlinks (that is all the watcher looks at), the
watching is driven one step at a time with a fake clock, and the starting and closing is also tried on real processes.
Nothing here touches the real ~/.config, ~/.local/state or /dev."""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

if sys.platform == "win32":
    raise unittest.SkipTest("the USB launcher reads /dev/disk, so it is Linux only")

from babyai import usbwatch  # noqa: E402
from babyai.config.loader import load_config  # noqa: E402
from babyai.usbwatch import Watcher  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def make_disk(base: Path, *, label: str = "MYSTICK", uuid: str | None = "ABCD-1234", ident: str = "SanDisk_Cruzer_Blade_4C53-0:0", device: str = "sdb", gigabytes: float = 16) -> None:
    """A /dev/disk like the one udev makes, in ``base``: one USB drive with one partition, and one internal disk."""
    for sub in ("dev", "disk/by-id", "disk/by-uuid", "disk/by-label", f"sys/{device}"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    (base / "dev" / device).write_text("")
    (base / "dev" / f"{device}1").write_text("")
    (base / "sys" / device / "size").write_text(str(int(gigabytes * 1e9 / 512)))
    (base / "disk/by-id" / f"usb-{ident}").symlink_to(f"../../dev/{device}")
    (base / "disk/by-id" / f"usb-{ident}-part1").symlink_to(f"../../dev/{device}1")
    if uuid:
        (base / "disk/by-uuid" / uuid).symlink_to(f"../../dev/{device}1")
    if label:
        (base / "disk/by-label" / label).symlink_to(f"../../dev/{device}1")
    # the computer's own disk is also in there, and must never be offered
    (base / "dev" / "nvme0n1").write_text("")
    (base / "dev" / "nvme0n1p2").write_text("")
    if not (base / "disk/by-id" / "nvme-Samsung_SSD").exists():
        (base / "disk/by-id" / "nvme-Samsung_SSD").symlink_to("../../dev/nvme0n1")
        (base / "disk/by-uuid" / "1111-2222").symlink_to("../../dev/nvme0n1p2")


def unplug(base: Path, device: str = "sdb") -> None:
    for link in (base / "disk").glob("*/*"):
        if os.path.realpath(link).startswith(str(base / "dev" / device)):
            link.unlink()
    for node in (base / "dev").glob(f"{device}*"):
        node.unlink()


class Disk(unittest.TestCase):
    """A temporary /dev/disk, /sys/block and home, with the module pointed at them."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name) / "fake"
        self.base.mkdir()
        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir()
        self.config = self.home / "config.toml"
        patches = [
            mock.patch.object(usbwatch, "DISK", self.base / "disk"),
            mock.patch.object(usbwatch, "SYS_BLOCK", self.base / "sys"),
            mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.home / "state"), "XDG_CONFIG_HOME": str(self.home / "cfg"), "XDG_DATA_HOME": str(self.home / "data")}),
            mock.patch.object(usbwatch, "_manager_environment", return_value={}),  # (the real systemd user manager is never asked)
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def run_cli(self, *argv: str) -> tuple[int, str]:
        out = StringIO()
        with redirect_stdout(out):
            code = usbwatch.main(list(argv))
        return code, out.getvalue()


# ---------------------------------------------------------------------------------------------- which drive


class SpecTests(Disk):
    def test_the_three_kinds_are_understood(self) -> None:
        self.assertEqual(usbwatch.parse_spec("uuid:ABCD-1234"), ("uuid", "ABCD-1234"))
        self.assertEqual(usbwatch.parse_spec(" label:MY\\x20STICK "), ("label", "MY\\x20STICK"))
        self.assertEqual(usbwatch.parse_spec("id:usb-SanDisk_X-0:0"), ("id", "usb-SanDisk_X-0:0"))

    def test_anything_else_is_refused(self) -> None:
        for bad in ("", "uuid:", ":abc", "serial:123", "ABCD-1234", "uuid:../x", "uuid:a/b", "label:..", "label:.", "uuid:" + "a" * 201, "uuid:a\0b"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                usbwatch.parse_spec(bad)

    def test_a_name_can_never_point_outside_the_disk_folder(self) -> None:
        (self.base / "secret").write_text("")
        with self.assertRaises(ValueError):
            usbwatch.is_present("uuid:../../secret")

    def test_present_follows_the_drive_in_and_out(self) -> None:
        self.assertFalse(usbwatch.is_present("uuid:ABCD-1234"))
        make_disk(self.base)
        self.assertTrue(usbwatch.is_present("uuid:ABCD-1234"))
        self.assertTrue(usbwatch.is_present("label:MYSTICK"))
        self.assertTrue(usbwatch.is_present("id:usb-SanDisk_Cruzer_Blade_4C53-0:0"))
        self.assertFalse(usbwatch.is_present("uuid:SOMETHING-ELSE"))
        unplug(self.base)
        self.assertFalse(usbwatch.is_present("uuid:ABCD-1234"))

    def test_a_link_left_pointing_at_nothing_is_not_a_drive(self) -> None:
        make_disk(self.base)
        (self.base / "dev" / "sdb1").unlink()  # udev removes the links a moment after the device goes; until then they dangle
        self.assertTrue(os.path.islink(self.base / "disk/by-uuid/ABCD-1234"))
        self.assertFalse(usbwatch.is_present("uuid:ABCD-1234"))


class ListTests(Disk):
    def test_only_usb_drives_are_offered_with_the_ways_to_recognise_each(self) -> None:
        make_disk(self.base)
        found = usbwatch.drives()
        self.assertEqual(len(found), 1)  # not the internal disk, and not the partition as a second drive
        drive = found[0]
        self.assertEqual(drive.name, "SanDisk Cruzer Blade 4C53")
        self.assertEqual(drive.size, "16.0 GB")
        self.assertEqual(drive.specs, ["uuid:ABCD-1234", "label:MYSTICK", "id:usb-SanDisk_Cruzer_Blade_4C53-0:0"])
        self.assertEqual(drive.device, str(self.base / "dev" / "sdb"))

    def test_a_drive_without_a_label_or_filesystem_can_still_be_found_by_what_it_is(self) -> None:
        make_disk(self.base, uuid=None, label="")
        self.assertEqual(usbwatch.drives()[0].specs, ["id:usb-SanDisk_Cruzer_Blade_4C53-0:0"])

    def test_two_drives_are_told_apart(self) -> None:
        make_disk(self.base)
        make_disk(self.base, label="OTHER", uuid="9999-8888", ident="Kingston_DT-0:0", device="sdc", gigabytes=0.5)
        found = usbwatch.drives()
        self.assertEqual([d.specs[0] for d in found], ["uuid:9999-8888", "uuid:ABCD-1234"])  # (by-id order)
        self.assertEqual(found[0].size, "500 MB")
        self.assertNotIn("uuid:ABCD-1234", found[0].specs)

    def test_escaped_characters_are_read_back_in_the_name_but_the_spec_stays_exactly_as_the_file_is_called(self) -> None:
        make_disk(self.base, label="My\\x20Stick", ident="Some\\x20Maker_Disk-0:0")
        drive = usbwatch.drives()[0]
        self.assertEqual(drive.name, "Some Maker Disk")
        self.assertIn("label:My\\x20Stick", drive.specs)
        self.assertTrue(usbwatch.is_present("label:My\\x20Stick"))

    def test_nothing_plugged_in_is_an_empty_list_not_an_error(self) -> None:
        self.assertEqual(usbwatch.drives(), [])
        code, out = self.run_cli("list")
        self.assertEqual(code, 1)
        self.assertIn("No USB drives found", out)

    def test_list_prints_what_pair_needs(self) -> None:
        make_disk(self.base)
        code, out = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("1) SanDisk Cruzer Blade 4C53", out)
        self.assertIn("uuid:ABCD-1234", out)


# ---------------------------------------------------------------------------------------------- the watching


class FakeChild:
    def __init__(self) -> None:
        self.running = True

    def poll(self) -> int | None:
        return None if self.running else 0


class Rig:
    """A watcher with a drive that can be pushed in and out, a clock that is moved by hand, and children that are only counted."""

    def __init__(self, **options: object) -> None:
        self.drive = False
        self.now = 100.0
        self.children: list[FakeChild] = []
        self.stopped: list[FakeChild] = []
        self.messages: list[str] = []
        self.elsewhere = False
        self.fail_to_start = False
        self.fail_to_stop = False
        settings = {"grace": 3.0, **options}
        self.watcher = Watcher(self._present, self._launch, self._stop, elsewhere=lambda: self.elsewhere, clock=lambda: self.now, log=self.messages.append, **settings)  # type: ignore[arg-type]

    def _present(self) -> bool:
        return self.drive

    def _launch(self) -> FakeChild:
        if self.fail_to_start:
            raise OSError("no such python")
        child = FakeChild()
        self.children.append(child)
        return child

    def _stop(self, child: FakeChild) -> None:
        if self.fail_to_stop:
            raise OSError("not permitted")
        self.stopped.append(child)
        child.running = False

    def tick(self, seconds: float = 1.0) -> None:
        self.now += seconds
        self.watcher.step()

    def pull_out(self, seconds: float) -> None:
        """The drive is taken out: the poll that notices it, then ``seconds`` more of it staying out."""
        self.drive = False
        self.tick(0)
        self.tick(seconds)


class WatcherTests(unittest.TestCase):
    def test_nothing_happens_without_the_drive(self) -> None:
        rig = Rig()
        for _ in range(10):
            rig.tick()
        self.assertEqual((rig.children, rig.stopped), ([], []))

    def test_plugging_it_in_starts_him_once_and_only_once(self) -> None:
        rig = Rig()
        rig.tick()
        rig.drive = True
        for _ in range(10):
            rig.tick()
        self.assertEqual(len(rig.children), 1)

    def test_taking_it_out_closes_that_same_one_after_the_grace_time(self) -> None:
        rig = Rig()
        rig.tick()
        rig.drive = True
        rig.tick()
        rig.pull_out(2)
        self.assertEqual(rig.stopped, [], "closed before the grace time was up")
        rig.tick(1)
        self.assertEqual(rig.stopped, rig.children)
        self.assertFalse(rig.children[0].running)

    def test_a_flaky_connection_does_not_close_him(self) -> None:
        rig = Rig()
        rig.drive = True
        rig.tick()
        for _ in range(5):
            rig.pull_out(2.5)  # gone for less than the grace time, over and over
            rig.drive = True
            rig.tick(0.5)
        self.assertEqual(rig.stopped, [])
        self.assertEqual(len(rig.children), 1, "a drive that blinked must not start a second one")

    def test_the_grace_time_restarts_each_time_it_goes(self) -> None:
        rig = Rig()
        rig.drive = True
        rig.tick()
        rig.pull_out(2)
        rig.drive = True
        rig.tick(1)
        rig.pull_out(2)  # 2s gone again, but 5s since the first time
        self.assertEqual(rig.stopped, [])
        rig.tick(1)
        self.assertEqual(len(rig.stopped), 1)

    def test_plugging_it_in_again_starts_him_again(self) -> None:
        rig = Rig()
        rig.drive = True
        rig.tick()
        rig.pull_out(5)
        rig.drive = True
        rig.tick()
        self.assertEqual(len(rig.children), 2)
        self.assertEqual(len(rig.stopped), 1)
        self.assertTrue(rig.children[1].running)

    def test_the_drive_already_in_when_the_watcher_starts_launches_him_by_default(self) -> None:
        rig = Rig()
        rig.drive = True
        rig.tick()
        self.assertEqual(len(rig.children), 1)

    def test_launch_if_present_off_waits_for_the_next_plug_in(self) -> None:
        rig = Rig(launch_if_present=False)
        rig.drive = True
        for _ in range(3):
            rig.tick()
        self.assertEqual(rig.children, [])
        self.assertTrue(any("launch_if_present" in m for m in rig.messages))
        rig.pull_out(5)
        rig.drive = True
        rig.tick()
        self.assertEqual(len(rig.children), 1)

    def test_launch_if_present_off_does_not_hold_back_a_drive_that_was_absent_at_first(self) -> None:
        rig = Rig(launch_if_present=False)
        rig.tick()
        rig.drive = True
        rig.tick()
        self.assertEqual(len(rig.children), 1)

    def test_if_he_is_closed_by_hand_he_stays_closed_until_the_drive_is_plugged_in_again(self) -> None:
        rig = Rig()
        rig.drive = True
        rig.tick()
        rig.children[0].running = False  # closed from his own window
        for _ in range(10):
            rig.tick()
        self.assertEqual(len(rig.children), 1)
        rig.pull_out(5)
        self.assertEqual(rig.stopped, [], "nothing left to close")
        rig.drive = True
        rig.tick()
        self.assertEqual(len(rig.children), 2)

    def test_close_on_remove_off_leaves_him_running_and_does_not_start_a_second(self) -> None:
        rig = Rig(close_on_remove=False)
        rig.drive = True
        rig.tick()
        rig.pull_out(5)
        self.assertEqual(rig.stopped, [])
        self.assertTrue(rig.children[0].running)
        rig.drive = True
        rig.tick()
        self.assertEqual(len(rig.children), 1)

    def test_a_start_that_fails_is_reported_and_not_retried_every_second(self) -> None:
        rig = Rig()
        rig.fail_to_start = True
        rig.drive = True
        for _ in range(20):
            rig.tick()
        self.assertEqual([m for m in rig.messages if "Could not start him" in m], ["Could not start him: no such python"])
        rig.pull_out(5)
        self.assertEqual(rig.stopped, [])  # nothing was started, so nothing is closed
        rig.fail_to_start = False
        rig.drive = True
        rig.tick()
        self.assertEqual(len(rig.children), 1, "the next plug-in tries again")

    def test_if_he_is_already_running_a_second_is_not_started_and_the_other_is_never_closed(self) -> None:
        rig = Rig()
        rig.elsewhere = True
        rig.drive = True
        rig.tick()
        self.assertEqual(rig.children, [])
        self.assertTrue(any("already running" in m for m in rig.messages))
        rig.pull_out(5)
        self.assertEqual(rig.stopped, [])

    def test_only_the_one_it_started_is_closed(self) -> None:
        rig = Rig()
        rig.drive = True
        rig.tick()
        mine = rig.children[0]
        rig.elsewhere = True  # somebody else starts one too; the watcher has no claim on it
        rig.pull_out(5)
        self.assertEqual(rig.stopped, [mine])

    def test_a_close_that_fails_is_reported_and_the_watching_goes_on(self) -> None:
        rig = Rig()
        rig.fail_to_stop = True
        rig.drive = True
        rig.tick()
        rig.pull_out(5)
        self.assertTrue(any("Could not close him" in m for m in rig.messages))
        self.assertTrue(rig.children[0].running)
        rig.fail_to_stop = False
        rig.drive = True
        rig.tick()
        self.assertEqual(len(rig.children), 1, "he is still up, so no second one beside him")
        rig.pull_out(5)
        self.assertEqual(rig.stopped, rig.children, "and the next time the drive goes he is closed")

    def test_it_says_what_it_does_in_the_log(self) -> None:
        rig = Rig()
        rig.drive = True
        rig.tick()
        rig.pull_out(5)
        self.assertEqual(rig.messages, ["The drive is in: started him.", "The drive is out: closed him."])


class WatchLoopTests(unittest.TestCase):
    def test_a_step_that_goes_wrong_is_reported_once_and_the_loop_goes_on(self) -> None:
        steps, messages, done = [], [], threading.Event()

        class Broken:
            def step(self) -> None:
                steps.append(1)
                if len(steps) == 5:
                    done.set()
                raise RuntimeError("boom")

        usbwatch.watch(Broken(), done, 0.001, messages.append)  # type: ignore[arg-type]
        self.assertEqual(len(steps), 5)
        self.assertEqual(messages, ["Something went wrong while watching: boom"])

    def test_it_stops_when_told_to(self) -> None:
        done = threading.Event()
        done.set()
        count = []
        usbwatch.watch(mock.Mock(step=lambda: count.append(1)), done, 0.001)
        self.assertEqual(count, [])


# ---------------------------------------------------------------------------------------------- closing him for real


def reap(process: subprocess.Popen) -> None:
    process.kill()
    process.wait()


def gone(pid: int) -> bool:
    """No such process, or only the zombie of one nobody has collected yet."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return True
    return state == "Z"


class StopProcessTests(unittest.TestCase):
    def test_a_polite_process_is_closed_with_sigterm(self) -> None:
        process = subprocess.Popen(["sleep", "60"], start_new_session=True)
        started = time.monotonic()
        usbwatch.stop_process(process, wait=5)
        self.assertEqual(process.returncode, -signal.SIGTERM)
        self.assertLess(time.monotonic() - started, 3)

    def test_one_that_needs_a_moment_to_close_gets_it(self) -> None:
        marker = Path(tempfile.mkdtemp()) / "saved"
        self.addCleanup(lambda: (marker.unlink(missing_ok=True), marker.parent.rmdir()))
        code = (
            "import pathlib, signal, sys, time\n"
            "def bye(*_):\n"
            "    time.sleep(0.4)  # saving what he was doing\n"
            "    pathlib.Path(sys.argv[1]).write_text('saved')\n"
            "    sys.exit(0)\n"
            "signal.signal(signal.SIGTERM, bye)\n"
            "print('ready', flush=True)\n"
            "time.sleep(60)\n"
        )
        process = subprocess.Popen([sys.executable, "-c", code, str(marker)], stdout=subprocess.PIPE, text=True, start_new_session=True)
        self.assertEqual(process.stdout.readline().strip(), "ready")  # type: ignore[union-attr]
        usbwatch.stop_process(process, wait=5)
        self.assertEqual(process.returncode, 0, "he should have been left to finish, not killed")
        self.assertEqual(marker.read_text(), "saved")
        process.stdout.close()  # type: ignore[union-attr]

    def test_one_that_ignores_sigterm_is_killed_and_so_is_what_it_started(self) -> None:
        code = (
            "import signal, subprocess, sys\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "child = subprocess.Popen(['sleep', '60'])\n"
            "print(child.pid, flush=True)\n"
            "child.wait()\n"
        )
        process = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, start_new_session=True)
        grandchild = int(process.stdout.readline())  # type: ignore[union-attr]
        usbwatch.stop_process(process, wait=0.3)
        self.assertEqual(process.returncode, -signal.SIGKILL)
        deadline = time.monotonic() + 3
        while not gone(grandchild) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(gone(grandchild), "what he started should not be left behind")
        process.stdout.close()  # type: ignore[union-attr]

    def test_a_process_that_is_not_in_a_group_of_its_own_is_closed_alone_and_our_own_group_is_left_alone(self) -> None:
        process = subprocess.Popen(["sleep", "60"])  # same group as this test run: killing "its group" would be killing the tests
        self.assertNotEqual(os.getpgid(process.pid), process.pid)
        usbwatch.stop_process(process, wait=5)
        self.assertEqual(process.returncode, -signal.SIGTERM)

    def test_one_that_is_already_gone_is_fine(self) -> None:
        process = subprocess.Popen(["true"])
        process.wait()
        usbwatch.stop_process(process)
        self.assertEqual(process.returncode, 0)


class SpawnTests(Disk):
    def test_he_is_started_in_a_session_of_his_own_with_his_output_kept(self) -> None:
        directory = self.home / "state"
        process = usbwatch.spawn([sys.executable, "-c", "import os; print('hello', os.getsid(0) == os.getpid())"], ROOT, directory)
        process.wait(timeout=10)
        self.assertEqual((directory / "babyai-from-usb.log").read_text().strip(), "hello True")

    def test_a_failure_to_start_is_an_error_for_the_watcher_to_report(self) -> None:
        with self.assertRaises(OSError):
            usbwatch.spawn(["/no/such/python", "-m", "babyai"], ROOT, self.home / "state")

    def test_a_value_of_none_takes_a_variable_out_of_his_environment(self) -> None:
        directory = self.home / "state"
        code = "import os; print(os.environ.get('XAUTHORITY', 'gone'), os.environ.get('MINE', 'unset'))"
        with mock.patch.dict(os.environ, {"XAUTHORITY": "/old/stale"}):
            usbwatch.spawn([sys.executable, "-c", code], ROOT, directory, {"XAUTHORITY": None, "MINE": "yes"}).wait(timeout=10)
        self.assertEqual((directory / "babyai-from-usb.log").read_text().strip(), "gone yes")

    def test_his_log_does_not_grow_without_end(self) -> None:
        directory = self.home / "state"
        directory.mkdir()
        (directory / "babyai-from-usb.log").write_text("x" * (usbwatch.MAX_LOG_BYTES + 1))
        usbwatch.spawn([sys.executable, "-c", "print('fresh')"], ROOT, directory).wait(timeout=10)
        self.assertEqual((directory / "babyai-from-usb.log").read_text().strip(), "fresh")


class FakeProc(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.proc = Path(self._tmp.name)

    def add(self, pid: str, *argv: str) -> None:
        (self.proc / pid).mkdir()
        (self.proc / pid / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")

    def test_he_is_found_when_he_is_running(self) -> None:
        self.add("4242", "/usr/bin/python3", "-m", "babyai", "--desktop", "--config", "/x/config.toml")
        self.assertTrue(usbwatch.another_babyai_running(self.proc, me=1))

    def test_the_watcher_itself_and_other_commands_are_not_him(self) -> None:
        self.add("10", "python3", "-m", "babyai.usbwatch", "run", "--desktop")  # (a different module: not him)
        self.add("11", "python3", "-m", "babyai", "ask", "hello")  # babyai, but not the overlay
        self.add("12", "python3", "script.py", "babyai", "--desktop")  # no -m babyai
        self.add("13", "vim", "babyai", "--desktop")
        (self.proc / "self").mkdir()
        (self.proc / "14").mkdir()  # no cmdline (a process that ended while we looked)
        self.assertFalse(usbwatch.another_babyai_running(self.proc, me=1))

    def test_our_own_process_does_not_count(self) -> None:
        self.add("4242", "python3", "-m", "babyai", "--desktop")
        self.assertFalse(usbwatch.another_babyai_running(self.proc, me=4242))

    def test_somebody_elses_does_not_count(self) -> None:
        self.add("4242", "python3", "-m", "babyai", "--desktop")
        with mock.patch.object(usbwatch.os, "getuid", return_value=os.getuid() + 1):
            self.assertFalse(usbwatch.another_babyai_running(self.proc, me=1))

    def test_no_proc_is_just_no(self) -> None:
        self.assertFalse(usbwatch.another_babyai_running(self.proc / "nowhere"))


# ---------------------------------------------------------------------------------------------- setting it up


class ConfigTests(Disk):
    def test_the_defaults_are_off_until_a_drive_is_paired(self) -> None:
        usb = load_config(str(self.home / "none.toml"))["usb"]
        self.assertEqual(usb["drive"], "")
        self.assertTrue(usb["launch_if_present"] and usb["close_on_remove"])
        self.assertEqual(usb["grace_seconds"], 3)

    def test_writing_the_drive_keeps_everything_else_and_one_backup_of_the_original(self) -> None:
        original = '# mine\n[llm]\nmodel = "llama3.1:8b"\n\n[usb]\ngrace_seconds = 7\n'
        self.config.write_text(original)
        usbwatch.write_setting(self.config, "usb", "drive", "uuid:ABCD-1234")
        usbwatch.write_setting(self.config, "usb", "drive", "label:OTHER")
        text = self.config.read_text()
        self.assertIn("# mine", text)
        self.assertIn("grace_seconds = 7", text)
        loaded = load_config(str(self.config))
        self.assertEqual(loaded["usb"]["drive"], "label:OTHER")
        self.assertEqual(loaded["usb"]["grace_seconds"], 7)
        self.assertEqual(loaded["llm"]["model"], "llama3.1:8b")
        self.assertEqual(self.config.with_name("config.toml.bak").read_text(), original, "the backup is of what the user had, not of the second write")
        self.assertFalse(self.config.with_name("config.toml.tmp").exists())

    def test_a_config_that_does_not_exist_yet_is_created_without_a_backup(self) -> None:
        usbwatch.write_setting(self.config, "usb", "drive", "uuid:ABCD-1234")
        self.assertEqual(load_config(str(self.config))["usb"]["drive"], "uuid:ABCD-1234")
        self.assertFalse(self.config.with_name("config.toml.bak").exists())

    def test_a_name_with_quotes_and_backslashes_survives_the_trip(self) -> None:
        spec = 'label:My\\x20"Stick"'
        usbwatch.write_setting(self.config, "usb", "drive", spec)
        self.assertEqual(load_config(str(self.config))["usb"]["drive"], spec)


class PairTests(Disk):
    def setUp(self) -> None:
        super().setUp()
        make_disk(self.base)
        patch = mock.patch.object(usbwatch, "start_watcher")
        self.started = patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(usbwatch, "stop_watcher", return_value=False)
        patch.start()
        self.addCleanup(patch.stop)

    def drive(self) -> str:
        return load_config(str(self.config))["usb"]["drive"]

    def test_the_one_drive_plugged_in_is_paired_by_its_filesystem_id(self) -> None:
        code, out = self.run_cli("pair", "--config", str(self.config))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.drive(), "uuid:ABCD-1234")
        self.assertIn("SanDisk Cruzer Blade 4C53", out)
        self.assertIn("reformat", out)  # it says what a uuid means for them

    def test_it_installs_the_login_item_and_starts_the_watcher(self) -> None:
        self.run_cli("pair", "--config", str(self.config))
        entry = usbwatch.autostart_path()
        self.assertTrue(entry.exists())
        self.assertIn(str(self.home / "cfg" / "autostart"), str(entry))
        self.started.assert_called_once_with(self.config.resolve())

    def test_by_label_or_by_hardware_when_asked(self) -> None:
        self.run_cli("pair", "--config", str(self.config), "--by", "label")
        self.assertEqual(self.drive(), "label:MYSTICK")
        self.run_cli("pair", "--config", str(self.config), "--by", "id")
        self.assertEqual(self.drive(), "id:usb-SanDisk_Cruzer_Blade_4C53-0:0")

    def test_a_drive_without_a_filesystem_id_falls_back_to_its_label_then_to_what_it_is(self) -> None:
        unplug(self.base)
        make_disk(self.base, uuid=None, label="ONLYLABEL")
        self.run_cli("pair", "--config", str(self.config))
        self.assertEqual(self.drive(), "label:ONLYLABEL")
        unplug(self.base)
        make_disk(self.base, uuid=None, label="")
        self.run_cli("pair", "--config", str(self.config))
        self.assertEqual(self.drive(), "id:usb-SanDisk_Cruzer_Blade_4C53-0:0")

    def test_asking_for_a_way_the_drive_does_not_have_changes_nothing(self) -> None:
        unplug(self.base)
        make_disk(self.base, uuid=None, label="")
        code, out = self.run_cli("pair", "--config", str(self.config), "--by", "uuid")
        self.assertEqual(code, 1)
        self.assertIn("no uuid", out)
        self.assertFalse(self.config.exists())
        self.started.assert_not_called()

    def test_with_two_drives_you_choose(self) -> None:
        make_disk(self.base, label="OTHER", uuid="9999-8888", ident="Kingston_DT-0:0", device="sdc")
        code, _ = self.run_cli("pair", "--config", str(self.config), "--pick", "2")
        self.assertEqual((code, self.drive()), (0, "uuid:ABCD-1234"))  # by-id order: Kingston is 1, SanDisk is 2
        with mock.patch("builtins.input", return_value="1"):
            self.run_cli("pair", "--config", str(self.config))
        self.assertEqual(self.drive(), "uuid:9999-8888")

    def test_a_choice_that_is_not_on_the_list_changes_nothing(self) -> None:
        make_disk(self.base, label="OTHER", uuid="9999-8888", ident="Kingston_DT-0:0", device="sdc")
        for argv, answer in ((("--pick", "3"), None), ((), "x"), ((), "0")):
            with self.subTest(argv=argv, answer=answer), mock.patch("builtins.input", return_value=answer):
                code, _ = self.run_cli("pair", "--config", str(self.config), *argv)
                self.assertEqual(code, 1)
        self.assertFalse(self.config.exists())

    def test_pairing_by_hand_for_a_drive_that_is_not_in_right_now(self) -> None:
        unplug(self.base)
        code, _ = self.run_cli("pair", "--config", str(self.config), "--spec", "label:SCRATCH")
        self.assertEqual((code, self.drive()), (0, "label:SCRATCH"))

    def test_a_spec_that_is_not_one_is_refused_before_anything_is_written(self) -> None:
        code, out = self.run_cli("pair", "--config", str(self.config), "--spec", "uuid:../../etc/passwd")
        self.assertEqual(code, 2)
        self.assertIn("isn't a drive", out)
        self.assertFalse(self.config.exists())
        self.assertFalse(usbwatch.autostart_path().exists())

    def test_nothing_plugged_in_says_so(self) -> None:
        unplug(self.base)
        code, out = self.run_cli("pair", "--config", str(self.config))
        self.assertEqual(code, 1)
        self.assertIn("can't see a USB drive", out)
        self.assertFalse(self.config.exists())

    def test_no_install_only_remembers_the_drive(self) -> None:
        code, _ = self.run_cli("pair", "--config", str(self.config), "--no-install")
        self.assertEqual((code, self.drive()), (0, "uuid:ABCD-1234"))
        self.assertFalse(usbwatch.autostart_path().exists())
        self.started.assert_not_called()


class InstallTests(Disk):
    def setUp(self) -> None:
        super().setUp()
        for name, kwargs in (("start_watcher", {}), ("stop_watcher", {"return_value": False})):
            patch = mock.patch.object(usbwatch, name, **kwargs)
            setattr(self, name, patch.start())
            self.addCleanup(patch.stop)
        usbwatch.write_setting(self.config, "usb", "drive", "uuid:ABCD-1234")

    def test_the_login_item_runs_the_installed_watcher_with_this_config(self) -> None:
        code, _ = self.run_cli("install", "--config", str(self.config))
        self.assertEqual(code, 0)
        text = usbwatch.autostart_path().read_text()
        self.assertIn("[Desktop Entry]\nType=Application\n", text)
        self.assertIn("Terminal=false\n", text)
        self.assertIn(f"Path={usbwatch.stub_dir().resolve()}\n", text)
        exec_line = next(line for line in text.splitlines() if line.startswith("Exec="))[len("Exec="):]
        argv = shlex.split(exec_line)
        self.assertEqual(argv[:3], ["env", "PYTHONDONTWRITEBYTECODE=1", sys.executable])
        self.assertEqual(argv[3], str(usbwatch.stub_path()))
        self.assertEqual(argv[4:6], ["run", "--config"])
        self.assertEqual(argv[6], str(self.config.resolve()))
        self.assertTrue(usbwatch.stub_path().is_file())
        self.assertNotIn("from babyai", usbwatch.stub_path().read_text())

    def test_what_the_login_item_runs_is_something_the_command_line_accepts(self) -> None:
        self.run_cli("install", "--config", str(self.config))
        exec_line = next(l for l in usbwatch.autostart_path().read_text().splitlines() if l.startswith("Exec="))[len("Exec="):]
        argv = shlex.split(exec_line)
        args = usbwatch.build_parser().parse_args(argv[argv.index("run"):])
        self.assertEqual((args.command, args.config), ("run", str(self.config.resolve())))

    def test_installing_again_changes_nothing_and_restarts_the_watcher_so_it_has_the_new_settings(self) -> None:
        self.run_cli("install", "--config", str(self.config))
        first = usbwatch.autostart_path().read_text()
        self.run_cli("install", "--config", str(self.config))
        self.assertEqual(usbwatch.autostart_path().read_text(), first)
        self.assertEqual(self.stop_watcher.call_count, 2)
        self.assertEqual(self.start_watcher.call_count, 2)

    def test_nothing_is_installed_for_a_drive_that_was_never_paired(self) -> None:
        other = self.home / "other.toml"
        code, out = self.run_cli("install", "--config", str(other))
        self.assertEqual(code, 2)
        self.assertIn("No drive is paired", out)
        self.assertFalse(usbwatch.autostart_path().exists())
        self.start_watcher.assert_not_called()

    def test_a_path_with_spaces_is_quoted_as_one_argument(self) -> None:
        spaced = self.home / "my babyai" / "config.toml"
        spaced.parent.mkdir()
        usbwatch.write_setting(spaced, "usb", "drive", "uuid:ABCD-1234")
        self.run_cli("install", "--config", str(spaced))
        exec_line = next(l for l in usbwatch.autostart_path().read_text().splitlines() if l.startswith("Exec="))[len("Exec="):]
        self.assertIn(str(spaced.resolve()), shlex.split(exec_line))
        self.assertIn(f'"{spaced.resolve()}"', exec_line)

    def test_a_path_that_could_do_something_in_a_login_item_is_refused(self) -> None:
        for name in ("a%fb", "a$HOME", "a`id`", 'a"b', "a;b", "a\\b", "a&b", "a\nb"):
            with self.subTest(name=name):
                nasty = self.home / name / "config.toml"
                nasty.parent.mkdir()
                usbwatch.write_setting(nasty, "usb", "drive", "uuid:ABCD-1234")
                code, out = self.run_cli("install", "--config", str(nasty))
                self.assertEqual(code, 2)
                self.assertIn("can't safely put in a login item", out)
                self.assertFalse(usbwatch.autostart_path().exists())

    def test_uninstall_removes_it_and_stops_the_watcher(self) -> None:
        self.run_cli("install", "--config", str(self.config))
        self.stop_watcher.return_value = True
        code, out = self.run_cli("uninstall", "--config", str(self.config))
        self.assertEqual(code, 0)
        self.assertFalse(usbwatch.autostart_path().exists())
        self.assertIn("Removed the login item", out)
        self.assertIn("Stopped the watcher", out)

    def test_uninstall_when_there_is_nothing_to_remove(self) -> None:
        code, out = self.run_cli("uninstall", "--config", str(self.config))
        self.assertEqual(code, 0)
        self.assertIn("no login item", out)


class StatusTests(Disk):
    def test_it_says_what_is_set_up(self) -> None:
        code, out = self.run_cli("status", "--config", str(self.config))
        self.assertEqual(code, 0)
        self.assertIn("(none: run pair)", out)
        self.assertIn("not installed", out)
        self.assertIn("not running", out)
        usbwatch.write_setting(self.config, "usb", "drive", "uuid:ABCD-1234")
        self.assertIn("Drive plugged in now: no", self.run_cli("status", "--config", str(self.config))[1])
        make_disk(self.base)
        self.assertIn("Drive plugged in now: yes", self.run_cli("status", "--config", str(self.config))[1])

    def test_it_says_which_display_he_would_get_without_giving_away_the_authority_file(self) -> None:
        cookie = self.home / "xauth_private_name"
        cookie.write_text("SECRETCOOKIE")
        outside = {key: value for key, value in os.environ.items() if key not in usbwatch.DISPLAY_KEYS}  # (a real X server in the test run must not leak in)
        with mock.patch.dict(os.environ, outside, clear=True):
            with mock.patch.object(usbwatch, "_manager_environment", return_value={"DISPLAY": ":0", "XAUTHORITY": str(cookie)}):
                out = self.run_cli("status", "--config", str(self.config))[1]
        self.assertIn("DISPLAY=:0", out)
        self.assertIn("X authority file: found", out)
        self.assertNotIn("SECRETCOOKIE", out)
        self.assertNotIn("xauth_private_name", out)

    def test_it_shows_the_end_of_the_log(self) -> None:
        directory = self.home / "state" / "babyai"
        directory.mkdir(parents=True)
        (directory / "usbwatch.log").write_text("".join(f"line {i}\n" for i in range(20)))
        out = self.run_cli("status", "--config", str(self.config))[1]
        self.assertIn("line 19", out)
        self.assertNotIn("line 14", out)


class OneWatcherTests(Disk):
    def test_a_second_watcher_cannot_take_the_lock_until_the_first_lets_go(self) -> None:
        directory = self.home / "state"
        first = usbwatch.take_lock(directory)
        self.assertIsNotNone(first)
        self.assertEqual((directory / "usbwatch.pid").read_text().strip(), str(os.getpid()))
        self.assertIsNone(usbwatch.take_lock(directory))
        first.close()
        again = usbwatch.take_lock(directory)
        self.assertIsNotNone(again)
        again.close()

    def test_a_pid_that_is_not_a_watcher_is_never_signalled(self) -> None:
        directory = self.home / "state"
        directory.mkdir()
        bystander = subprocess.Popen(["sleep", "60"])
        self.addCleanup(reap, bystander)
        (directory / "usbwatch.pid").write_text(f"{bystander.pid}\n")  # a stale file whose pid has been given to something else
        self.assertIsNone(usbwatch.running_watcher(directory))
        self.assertFalse(usbwatch.stop_watcher(directory))
        self.assertIsNone(bystander.poll())

    def test_garbage_in_the_pid_file_is_no_watcher(self) -> None:
        directory = self.home / "state"
        directory.mkdir()
        for text in ("", "abc", "99999999999\n"):
            (directory / "usbwatch.pid").write_text(text)
            self.assertIsNone(usbwatch.running_watcher(directory))

    def test_a_running_watcher_is_found_and_stopped(self) -> None:
        directory = self.home / "state"
        directory.mkdir()
        watcher = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "babyai.usbwatch"])
        self.addCleanup(reap, watcher)
        (directory / "usbwatch.pid").write_text(f"{watcher.pid}\n")
        deadline = time.monotonic() + 10  # (until the new process has really become python: before that /proc shows its parent's command line)
        while time.monotonic() < deadline and b"babyai.usbwatch" not in Path(f"/proc/{watcher.pid}/cmdline").read_bytes():
            time.sleep(0.02)
        self.assertEqual(usbwatch.running_watcher(directory), watcher.pid)
        stopper = threading.Thread(target=watcher.wait)  # (reaps it, so it does not stay as a zombie that still looks alive)
        stopper.start()
        self.assertTrue(usbwatch.stop_watcher(directory))
        stopper.join(5)
        self.assertEqual(watcher.returncode, -signal.SIGTERM)


class RunTests(Disk):
    """``run`` itself, without letting it loop or touch the signal handlers of the test run."""

    def run_it(self) -> tuple[int, str, Watcher | None]:
        seen: list[Watcher] = []
        with mock.patch.object(usbwatch, "watch", lambda watcher, *a, **k: seen.append(watcher)), mock.patch.object(usbwatch.signal, "signal"):
            code, out = self.run_cli("run", "--config", str(self.config))
        return code, out, (seen[0] if seen else None)

    def test_no_drive_paired_says_how_to_and_does_not_watch(self) -> None:
        code, out, watcher = self.run_it()
        self.assertEqual(code, 2)
        self.assertIn("pair", out)
        self.assertIsNone(watcher)

    def test_a_spec_that_was_edited_into_nonsense_is_reported_not_run(self) -> None:
        usbwatch.write_setting(self.config, "usb", "drive", "../../x")
        code, out, watcher = self.run_it()
        self.assertEqual(code, 2)
        self.assertIn("isn't a drive", out)
        self.assertIsNone(watcher)

    def test_the_settings_in_the_config_are_the_ones_used(self) -> None:
        usbwatch.write_setting(self.config, "usb", "drive", "uuid:ABCD-1234")
        self.config.write_text(self.config.read_text() + "grace_seconds = 9\nlaunch_if_present = false\nclose_on_remove = false\n")
        code, _, watcher = self.run_it()
        self.assertEqual(code, 0)
        assert watcher is not None
        self.assertEqual((watcher.grace, watcher.launch_if_present, watcher.close_on_remove), (9.0, False, False))
        self.assertFalse(watcher._present())
        make_disk(self.base)
        self.assertTrue(watcher._present())

    def test_he_is_started_as_the_overlay_from_this_checkout_with_the_same_config(self) -> None:
        usbwatch.write_setting(self.config, "usb", "drive", "uuid:ABCD-1234")
        _, _, watcher = self.run_it()
        assert watcher is not None
        with mock.patch.object(usbwatch, "spawn") as spawn, mock.patch.object(usbwatch, "wait_for_display", return_value={"DISPLAY": ":0"}):
            watcher._launch_fn()
        command, cwd, directory, env = spawn.call_args.args
        self.assertEqual(command, [sys.executable, "-m", "babyai", "--desktop", "--config", str(self.config.resolve())])
        self.assertEqual(cwd, ROOT)
        self.assertEqual(directory, self.home / "state" / "babyai")
        self.assertEqual(env, {"DISPLAY": ":0"})  # (the session's current display, not what the watcher started with)

    def test_it_says_so_when_a_watcher_is_already_running(self) -> None:
        usbwatch.write_setting(self.config, "usb", "drive", "uuid:ABCD-1234")
        lock = usbwatch.take_lock(usbwatch.state_dir())
        self.addCleanup(lock.close)
        code, out, watcher = self.run_it()
        self.assertEqual(code, 0)
        self.assertIn("already running", out)
        self.assertIsNone(watcher)

    def test_it_logs_to_a_file_in_the_state_folder(self) -> None:
        usbwatch.write_setting(self.config, "usb", "drive", "uuid:ABCD-1234")
        self.run_it()
        self.assertIn("Watching for uuid:ABCD-1234", (self.home / "state" / "babyai" / "usbwatch.log").read_text())

    def test_the_real_command_without_a_drive_exits_with_an_explanation(self) -> None:
        done = subprocess.run(
            [sys.executable, "-m", "babyai.usbwatch", "run", "--config", str(self.config)], cwd=ROOT, capture_output=True, text=True, timeout=30,
            env={**os.environ, "XDG_STATE_HOME": str(self.home / "state")},
        )
        self.assertEqual(done.returncode, 2)
        self.assertIn("No drive is paired yet", done.stdout)


# ---------------------------------------------------------------------------------------------- portable mode


class PortableDisk(Disk):
    """A mounted fake stick with a BabyAI folder on it, and a pairing record with a trust manifest."""

    def setUp(self) -> None:
        super().setUp()
        make_disk(self.base)
        self.mount = self.home / "mnt"
        self.mount.mkdir()
        self.app = self.mount / "BabyAI"
        (self.app / "babyai").mkdir(parents=True)
        (self.app / "babyai" / "__init__.py").write_text("")
        (self.app / "babyai" / "a.py").write_text("A = 1\n")
        (self.app / "config.toml").write_text('[usb]\nmode = "portable"\ndrive = "uuid:ABCD-1234"\n')
        for name in ("", "cache", "habits", "personality", "reminders", "self"):
            (self.app / "data" / name).mkdir(parents=True, exist_ok=True)
        self.mountinfo = self.home / "mountinfo"
        self.mountinfo.write_text(f"36 35 8:49 / {self.mount} rw - vfat {self.base / 'dev' / 'sdb1'} rw\n")
        self._mountinfo_patch = mock.patch.object(usbwatch, "MOUNTINFO", self.mountinfo)
        self._mountinfo_patch.start()
        self.addCleanup(self._mountinfo_patch.stop)
        self.pairing_path = self.home / "cfg" / "babyai-usb" / "pairing.toml"
        self.pairing = {
            "mode": "portable", "drive": "uuid:ABCD-1234", "app_folder": "BabyAI",
            "grace_seconds": 3, "launch_if_present": True, "close_on_remove": True,
            "trust": usbwatch.manifest(self.app),
        }
        usbwatch.save_pairing(self.pairing, self.pairing_path)

    def pairing_args(self) -> dict[str, object]:
        return {"pairing": str(self.pairing_path)}


class PortableTrustTests(PortableDisk):
    def test_a_changed_python_file_is_listed_and_refuses_launch(self) -> None:
        (self.app / "babyai" / "a.py").write_text("A = 2\n")
        self.assertEqual(usbwatch.trust_changes(self.pairing, self.app), ["babyai/a.py"])
        with self.assertRaisesRegex(usbwatch.LaunchError, "changed|trust"):
            usbwatch.launch_portable(self.pairing, self.home / "state", lambda message: None)

    def test_the_trust_command_prints_the_change_and_the_yes_updates_the_manifest(self) -> None:
        (self.app / "babyai" / "a.py").write_text("A = 2\n")
        out = StringIO()
        with redirect_stdout(out), mock.patch("builtins.input", return_value="yes"), mock.patch.object(usbwatch, "_can_ask", return_value=True):
            code = usbwatch.main(["trust", "--pairing", str(self.pairing_path)])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("babyai/a.py", out.getvalue())
        saved = usbwatch.load_pairing(self.pairing_path)
        self.assertEqual(saved["trust"]["babyai/a.py"], usbwatch._sha256(self.app / "babyai" / "a.py"))
        self.assertEqual(usbwatch.trust_changes(saved, self.app), [])

    def test_a_no_leaves_the_manifest_alone(self) -> None:
        (self.app / "babyai" / "a.py").write_text("A = 2\n")
        before = usbwatch.load_pairing(self.pairing_path)
        with redirect_stdout(StringIO()), mock.patch("builtins.input", return_value="no"), mock.patch.object(usbwatch, "_can_ask", return_value=True):
            code = usbwatch.main(["trust", "--pairing", str(self.pairing_path)])
        self.assertEqual(code, 1)
        self.assertEqual(usbwatch.load_pairing(self.pairing_path), before)

    def test_a_manifest_that_is_missing_refuses_until_trusted(self) -> None:
        pairing = {key: value for key, value in self.pairing.items() if key != "trust"}
        self.assertTrue(usbwatch.trust_changes(pairing, self.app)[0].startswith("(no trust manifest"))

    def test_a_missing_file_is_listed_and_cannot_be_trusted_without_input(self) -> None:
        (self.app / "babyai" / "a.py").unlink()
        before = usbwatch.load_pairing(self.pairing_path)
        out = StringIO()
        with redirect_stdout(out), mock.patch("builtins.input", side_effect=EOFError):
            code = usbwatch.main(["trust", "--pairing", str(self.pairing_path)])
        self.assertEqual(code, 1)
        self.assertIn("babyai/a.py", out.getvalue())
        self.assertEqual(usbwatch.load_pairing(self.pairing_path), before)

    def test_there_is_no_non_interactive_trust_flag(self) -> None:
        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            usbwatch.build_parser().parse_args(["trust", "--yes"])



class PortableMakeDriveTests(PortableDisk):
    def setUp(self) -> None:
        super().setUp()
        self.source = self.home / "checkout"
        for name in ("babyai", "tests", "capri", "assets"):
            (self.source / name).mkdir(parents=True)
        (self.source / "babyai" / "__init__.py").write_text("")
        (self.source / "babyai" / "main.py").write_text("print('hi')\n")
        (self.source / "config.toml").write_text('[llm]\nmodel = "test"\n')
        (self.source / "LICENSE").write_text("licence")
        (self.source / "NOTICE").write_text("notice")

    def test_data_copy_preserves_existing_usb_files_by_default(self) -> None:
        source_data = self.home / "source-data"
        destination = self.app / "data"

        (source_data / "habits").mkdir(parents=True)
        (source_data / "personality").mkdir()
        (source_data / "self" / "worktrees").mkdir(parents=True)

        (source_data / "habits" / "profile.txt").write_text("computer version")
        (source_data / "personality" / "new.txt").write_text("new file")
        (source_data / "self" / "settings.txt").write_text("computer settings")

        (destination / "habits" / "profile.txt").write_text("USB version")
        (destination / "habits" / "usb-only.txt").write_text("keep this")
        (destination / "self" / "settings.txt").write_text("USB settings")

        usbwatch._copy_data(source_data, destination, False, lambda _: None)

        self.assertEqual((destination / "habits" / "profile.txt").read_text(), "USB version")
        self.assertEqual((destination / "habits" / "usb-only.txt").read_text(), "keep this")
        self.assertEqual((destination / "personality" / "new.txt").read_text(), "new file")
        self.assertEqual((destination / "self" / "settings.txt").read_text(), "USB settings")
        self.assertFalse((destination / "self" / "worktrees").exists())

    def test_overwrite_data_replaces_matching_files_but_keeps_usb_only_files(self) -> None:
        source_data = self.home / "source-data"
        destination = self.app / "data"

        (source_data / "habits").mkdir(parents=True)
        (source_data / "habits" / "profile.txt").write_text("computer version")

        (destination / "habits" / "profile.txt").write_text("USB version")
        (destination / "habits" / "usb-only.txt").write_text("keep this")

        (source_data / "memory.db").write_text("source database")
        (destination / "memory.db").write_text("existing database")

        with mock.patch.object(usbwatch, "_copy_memory_database", return_value=True) as copy_memory:
            usbwatch._copy_data(source_data, destination, False, lambda _: None)
            copy_memory.assert_not_called()

            usbwatch._copy_data(source_data, destination, True, lambda _: None)
            copy_memory.assert_called_once()

        self.assertEqual((destination / "habits" / "profile.txt").read_text(), "computer version")
        self.assertEqual((destination / "habits" / "usb-only.txt").read_text(), "keep this")

    def test_it_refuses_a_non_empty_app_folder_without_force(self) -> None:
        self.app.mkdir(parents=True, exist_ok=True)
        (self.app / "old.txt").write_text("old")
        code, out = self.run_cli("make-drive", str(self.mount), "--source", str(self.source), "--uuid", "ABCD-1234", "--no-install", "--no-data", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("already has files", out)

    def test_it_copies_the_app_configuration_and_trust_but_not_the_host_data(self) -> None:
        target = self.home / "mnt2"
        target.mkdir()
        self.mountinfo.write_text(self.mountinfo.read_text() + f"37 35 8:50 / {target} rw - vfat {self.base / 'dev' / 'sdb1'} rw\n")
        code, out = self.run_cli(
            "make-drive", str(target), "--source", str(self.source), "--uuid", "ABCD-1234",
            "--no-install", "--no-data", "--yes", "--pairing", str(self.pairing_path),
        )
        self.assertEqual(code, 0, out)
        root = target / "BabyAI"
        self.assertTrue((root / "babyai" / "main.py").is_file())
        config = (root / "config.toml").read_text()
        self.assertIn('mode = "portable"', config)
        self.assertIn('drive = "uuid:ABCD-1234"', config)
        self.assertIn('model = "test"', config)
        self.assertTrue((root / "data" / "cache").is_dir())
        saved = usbwatch.load_pairing(self.pairing_path)
        self.assertEqual(saved["mode"], "portable")
        self.assertEqual(saved["trust"], usbwatch.manifest(root))
        self.assertEqual(usbwatch.trust_changes(saved, root), [])

    def test_the_trust_manifest_covers_tests_and_app_files(self) -> None:
        target = self.home / "mnt3"
        target.mkdir()
        self.mountinfo.write_text(self.mountinfo.read_text() + f"38 35 8:51 / {target} rw - vfat {self.base / 'dev' / 'sdb1'} rw\n")
        code, _ = self.run_cli(
            "make-drive", str(target), "--source", str(self.source), "--uuid", "ABCD-1234",
            "--no-install", "--no-data", "--yes", "--no-git", "--pairing", str(self.pairing_path),
        )
        self.assertEqual(code, 0)
        saved = usbwatch.load_pairing(self.pairing_path)
        self.assertIn("babyai/main.py", saved["trust"])
        self.assertIn("babyai/__init__.py", saved["trust"])


# ---------------------------------------------------------------------------------------------- all of it together


class EndToEndTests(Disk):
    def test_plug_in_starts_a_real_process_and_taking_it_out_ends_it(self) -> None:
        directory = self.home / "state"
        spec = "uuid:ABCD-1234"
        messages: list[str] = []
        watcher = Watcher(
            lambda: usbwatch.is_present(spec), lambda: usbwatch.spawn(["sleep", "60"], ROOT, directory), usbwatch.stop_process,
            grace=0.15, log=messages.append,
        )
        done = threading.Event()
        loop = threading.Thread(target=usbwatch.watch, args=(watcher, done, 0.02, messages.append))
        loop.start()
        self.addCleanup(lambda: (done.set(), loop.join(5), watcher.child and reap(watcher.child)))

        def until(condition, what: str) -> None:  # type: ignore[no-untyped-def]
            deadline = time.monotonic() + 10
            while not condition() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(condition(), what + f" ({messages})")

        time.sleep(0.2)
        self.assertIsNone(watcher.child, "started without the drive")

        make_disk(self.base)
        until(lambda: watcher.child is not None and watcher.child.poll() is None, "he did not start when the drive was plugged in")
        process = watcher.child
        time.sleep(0.3)
        self.assertIsNone(process.poll(), "he must keep running while the drive is in")

        unplug(self.base)
        until(lambda: process.poll() is not None, "he did not close when the drive was taken out")
        self.assertEqual(process.returncode, -signal.SIGTERM)

        make_disk(self.base)
        until(lambda: watcher.child is not None and watcher.child is not process and watcher.child.poll() is None, "he did not start again on the next plug-in")


# ---------------------------------------------------------------------------------------------- what review found


class TrustNeedsAKeyboardTests(PortableDisk):
    """Trusting changed files is the user's call: a script, or he himself through a shell, cannot say yes."""

    def test_without_a_keyboard_nothing_is_trusted_and_it_says_why(self) -> None:
        (self.app / "babyai" / "a.py").write_text("A = 2\n")
        before = usbwatch.load_pairing(self.pairing_path)
        with mock.patch.object(usbwatch, "_can_ask", return_value=False), mock.patch("builtins.input", return_value="yes") as ask:
            code, out = self.run_cli("trust", "--pairing", str(self.pairing_path))
        self.assertEqual(code, 1)
        self.assertIn("yourself", out)
        ask.assert_not_called()
        self.assertEqual(usbwatch.load_pairing(self.pairing_path), before)

    def test_there_is_no_flag_that_says_yes_for_you(self) -> None:
        with self.assertRaises(SystemExit), mock.patch("sys.stderr", new_callable=StringIO):
            usbwatch.main(["trust", "--yes", "--pairing", str(self.pairing_path)])

    def test_a_keyboard_is_a_terminal_on_stdin(self) -> None:
        with mock.patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            self.assertFalse(usbwatch._can_ask())
            stdin.isatty.return_value = True
            self.assertTrue(usbwatch._can_ask())


class ManifestParityTests(unittest.TestCase):
    """The installed launcher cannot import babyai.portable, so it has its own copy of the manifest rules: they must agree."""

    def test_both_make_the_same_manifest_for_a_tree_with_everything_in_it(self) -> None:
        from babyai import portable
        from tests.test_portable import build_stick_tree

        with tempfile.TemporaryDirectory() as tmp:
            root = build_stick_tree(Path(tmp))
            self.assertEqual(usbwatch.manifest(root), portable.manifest(root))


class MountParsingTests(Disk):
    """The user's own stick is called "BENNY_S AI": /proc/self/mountinfo writes that space as \\040."""

    def test_a_mount_point_with_a_space_in_it_is_read_back_whole(self) -> None:
        mount = self.home / "BENNY_S AI"
        mount.mkdir()
        info = self.home / "mountinfo"
        escaped = str(mount).replace(" ", "\\040")
        info.write_text(
            "26 1 0:23 / /run rw - tmpfs tmpfs rw\n"
            f"412 62 8:49 / {escaped} rw,nosuid,nodev,relatime shared:230 - vfat /dev/sdd1 rw,uid=1000,gid=1000,fmask=0022,dmask=0022,showexec,flush\n"
        )
        found = usbwatch.mounts(info)
        self.assertEqual([item.target for item in found], [Path("/run"), mount])
        stick = found[1]
        self.assertEqual((stick.source, stick.fstype), ("/dev/sdd1", "vfat"))
        self.assertIn("nosuid", stick.options)
        self.assertIn("showexec", stick.options)
        self.assertEqual(usbwatch.mount_point("/dev/sdd1", info), mount)
        self.assertEqual(usbwatch.mount_for_path(mount, info).fstype, "vfat")

    def test_a_device_that_is_not_mounted_has_no_mount_point(self) -> None:
        info = self.home / "mountinfo"
        info.write_text("26 1 0:23 / /run rw - tmpfs tmpfs rw\n")
        self.assertIsNone(usbwatch.mount_point("/dev/sdd1", info))
        self.assertEqual(usbwatch.mounts(self.home / "missing"), [])


class LaunchPortableTests(PortableDisk):
    def setUp(self) -> None:
        super().setUp()
        patches = [
            mock.patch.dict(os.environ, {"DISPLAY": ":0", "XDG_RUNTIME_DIR": str(self.home / "run")}),
            mock.patch.object(usbwatch, "choose_python", return_value="/usr/bin/python3"),
            mock.patch.object(usbwatch, "start_local_ollama", return_value=None),
            mock.patch.object(usbwatch, "spawn", return_value=mock.Mock(pid=4242)),
        ]
        self.spawn = patches[3].start()
        for patch in patches[:3]:
            patch.start()
        for patch in patches:
            self.addCleanup(patch.stop)
        self.state = self.home / "state" / "babyai"

    def test_he_is_started_from_the_drive_with_the_data_and_caches_on_the_drive(self) -> None:
        usbwatch.launch_portable(self.pairing, self.state, lambda message: None)
        command, cwd, _directory, env = self.spawn.call_args.args
        self.assertEqual(command, ["/usr/bin/python3", "-m", "babyai", "--desktop", "--config", str(self.app / "config.toml")])
        self.assertEqual(cwd, self.app)
        self.assertEqual(env["BABYAI_HOME"], str(self.app))
        self.assertEqual(env["XDG_CACHE_HOME"], str(self.app / "data" / "cache"))
        self.assertEqual(env["PYTHONPATH"], str(self.app))
        self.assertEqual(env["BABYAI_PAIRING"], str(usbwatch.pairing_path()))

    def test_he_is_given_the_sessions_current_display_not_the_stale_one_the_watcher_started_with(self) -> None:
        fresh = self.home / "run" / "xauth_fresh"
        fresh.parent.mkdir(parents=True, exist_ok=True)
        fresh.write_text("")
        with mock.patch.dict(os.environ, {"XAUTHORITY": str(self.home / "run" / "xauth_gone")}):
            with mock.patch.object(usbwatch, "_manager_environment", return_value={"DISPLAY": ":0", "XAUTHORITY": str(fresh)}):
                usbwatch.launch_portable(self.pairing, self.state, lambda message: None)
        env = self.spawn.call_args.args[3]
        self.assertEqual(env["XAUTHORITY"], str(fresh))
        self.assertEqual(env["BABYAI_HOME"], str(self.app))  # (and everything portable is still there)

    def test_a_stale_authority_file_nobody_can_replace_is_taken_out_not_passed_on(self) -> None:
        with mock.patch.dict(os.environ, {"XAUTHORITY": str(self.home / "run" / "xauth_gone")}):
            usbwatch.launch_portable(self.pairing, self.state, lambda message: None)
        self.assertIsNone(self.spawn.call_args.args[3]["XAUTHORITY"])

    def test_if_the_desktop_is_not_up_yet_he_waits_for_it_and_then_starts(self) -> None:
        answers = iter([{}, {}, {"DISPLAY": ":0"}])
        said: list[str] = []
        env = {key: value for key, value in os.environ.items() if key not in usbwatch.DISPLAY_KEYS}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(usbwatch, "_manager_environment", side_effect=lambda: next(answers)), mock.patch.object(usbwatch.time, "sleep"):
                usbwatch.launch_portable(self.pairing, self.state, said.append)
        self.spawn.assert_called_once()
        self.assertEqual(self.spawn.call_args.args[3]["DISPLAY"], ":0")
        self.assertEqual(sum("isn't ready yet" in line for line in said), 1)  # (said once, not once a second)

    def test_a_drive_taken_out_while_he_waited_for_the_desktop_is_not_launched_from(self) -> None:
        with mock.patch.object(usbwatch, "wait_for_display", side_effect=lambda *a, **k: unplug(self.base) or {"DISPLAY": ":0"}):
            with self.assertRaisesRegex(usbwatch.LaunchError, "taken out"):
                usbwatch.launch_portable(self.pairing, self.state, lambda message: None)
        self.spawn.assert_not_called()

    def test_no_desktop_at_all_is_explained_and_nothing_is_started(self) -> None:
        env = {key: value for key, value in os.environ.items() if key not in usbwatch.DISPLAY_KEYS}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(usbwatch.LaunchError, "no display"):
                usbwatch.launch_portable(self.pairing, self.state, lambda message: None, cancelled=lambda: True)
        self.spawn.assert_not_called()

    def test_bytecode_on_the_stick_is_never_read(self) -> None:
        usbwatch.launch_portable(self.pairing, self.state, lambda message: None)
        env = self.spawn.call_args.args[3]
        prefix = Path(env["PYTHONPYCACHEPREFIX"])
        self.assertNotIn(self.mount, prefix.parents)
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")

    def test_the_pid_is_remembered_for_eject(self) -> None:
        launched = usbwatch.launch_portable(self.pairing, self.state, lambda message: None)
        self.assertEqual(launched.pid, 4242)
        saved = usbwatch._read_child_state()
        self.assertEqual((saved["pid"], saved["root"]), (4242, str(self.app)))

    def test_a_drive_that_is_in_but_not_mounted_is_explained_not_launched(self) -> None:
        with mock.patch.object(usbwatch, "wait_for_mount", return_value=None):
            with self.assertRaisesRegex(usbwatch.LaunchError, "isn't mounted"):
                usbwatch.launch_portable(self.pairing, self.state, lambda message: None)
        self.spawn.assert_not_called()

    def test_a_folder_that_is_not_babyai_is_explained_not_launched(self) -> None:
        (self.app / "babyai" / "__init__.py").unlink()
        with self.assertRaisesRegex(usbwatch.LaunchError, "doesn't look like"):
            usbwatch.launch_portable(self.pairing, self.state, lambda message: None)
        self.spawn.assert_not_called()

    def test_changed_code_is_refused_before_anything_starts(self) -> None:
        (self.app / "babyai" / "a.py").write_text("A = 'evil'\n")
        with mock.patch.object(usbwatch, "notify") as notify:
            with self.assertRaisesRegex(usbwatch.LaunchError, "trust"):
                usbwatch.launch_portable(self.pairing, self.state, lambda message: None)
        notify.assert_called_once()
        self.spawn.assert_not_called()

    def test_a_swapped_bundled_program_is_refused_too(self) -> None:
        (self.app / "runtime" / "bin").mkdir(parents=True)
        (self.app / "runtime" / "bin" / "python3").write_text("real")
        usbwatch.save_pairing({**self.pairing, "trust": usbwatch.manifest(self.app)}, self.pairing_path)
        pairing = usbwatch.load_pairing(self.pairing_path)
        usbwatch.launch_portable(pairing, self.state, lambda message: None)  # trusted as it is
        (self.app / "runtime" / "bin" / "python3").write_text("swapped")
        with self.assertRaisesRegex(usbwatch.LaunchError, "runtime/"):
            usbwatch.launch_portable(pairing, self.state, lambda message: None)


class EjectTests(PortableDisk):
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(os.chdir, os.getcwd())  # (eject leaves the drive's folders so it can unmount)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.calls = self.home / "udisksctl.calls"
        tool = self.bin / "udisksctl"
        tool.write_text(f"#!/bin/sh\necho \"$@\" >> {shlex.quote(str(self.calls))}\nexit ${{UDISKS_EXIT:-0}}\n")
        tool.chmod(0o755)
        patch = mock.patch.dict(os.environ, {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"})
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(usbwatch, "stop_watcher", return_value=True)
        self.stop_watcher = patch.start()
        self.addCleanup(patch.stop)

    def start_app(self) -> subprocess.Popen:
        """Something whose command line is what he looks like when the launcher started him from this drive."""
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)", "babyai", "--desktop", "--config", str(self.app / "config.toml")],
            start_new_session=True,
        )
        self.addCleanup(lambda: (process.poll() is None and process.kill(), process.wait()))
        return process

    def eject(self, *argv: str) -> tuple[int, str]:
        return self.run_cli("eject", "--pairing", str(self.pairing_path), *argv)

    def test_he_is_closed_then_the_drive_is_unmounted(self) -> None:
        process = self.start_app()
        code, out = self.eject("--pid", str(process.pid))
        self.assertEqual(code, 0, out)
        self.assertIsNotNone(process.poll(), "he must be closed before the drive is let go")
        self.assertEqual(self.calls.read_text().split(), ["unmount", "-b", str(self.base / "dev" / "sdb1")])

    def test_the_launcher_keeps_watching_so_the_next_plug_in_starts_him_again(self) -> None:
        process = self.start_app()
        self.eject("--pid", str(process.pid))
        self.stop_watcher.assert_not_called()

    def test_a_process_that_is_not_him_is_left_alone(self) -> None:
        other = subprocess.Popen(["sleep", "60"], start_new_session=True)
        self.addCleanup(lambda: (other.poll() is None and other.kill(), other.wait()))
        _code, out = self.eject("--pid", str(other.pid))
        self.assertIn("left every process alone", out)
        self.assertIsNone(other.poll())

    def test_a_copy_of_him_running_from_another_folder_is_left_alone(self) -> None:
        elsewhere = self.home / "elsewhere"
        elsewhere.mkdir()
        other = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)", "babyai", "--desktop", "--config", str(elsewhere / "config.toml")],
            start_new_session=True,
        )
        self.addCleanup(lambda: (other.poll() is None and other.kill(), other.wait()))
        self.eject("--pid", str(other.pid))
        self.assertIsNone(other.poll())

    def test_a_drive_that_will_not_let_go_is_reported_and_not_forced(self) -> None:
        process = self.start_app()
        ticks = iter(range(0, 10_000, 40))  # a clock that runs fast, so the 20 s wait for the drive ends at once
        with mock.patch.dict(os.environ, {"UDISKS_EXIT": "1"}), mock.patch.object(usbwatch.time, "sleep"), \
                mock.patch.object(usbwatch.time, "monotonic", side_effect=lambda: float(next(ticks))):
            code, _out = self.eject("--pid", str(process.pid))
        self.assertEqual(code, 1)
        self.assertNotIn("power-off", self.calls.read_text())

    def test_without_udisksctl_it_says_so(self) -> None:
        process = self.start_app()
        with mock.patch.object(usbwatch.shutil, "which", return_value=None):
            code, out = self.eject("--pid", str(process.pid))
        self.assertEqual(code, 1)
        self.assertIn("udisksctl isn't installed", out)

    def test_a_key_only_pairing_has_nothing_to_eject(self) -> None:
        usbwatch.save_pairing({"mode": "key", "drive": "uuid:ABCD-1234", "config": str(self.config)}, self.pairing_path)
        code, out = self.eject()
        self.assertEqual(code, 1)
        self.assertIn("portable", out)


class FatStickGitTests(PortableDisk):
    """FAT shows every file as executable. git reads that as every file edited, and self-upgrade refuses a dirty folder."""

    def make_stick(self, fstype: str) -> Path:
        git = shutil.which("git")
        if not git:
            self.skipTest("git is not installed")
        source = self.home / "checkout"
        (source / "babyai").mkdir(parents=True)
        (source / "babyai" / "__init__.py").write_text("")
        (source / "babyai" / "main.py").write_text("print('hi')\n")
        for args in (("init", "-q"), ("add", "-A"), ("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")):
            subprocess.run([git, *args], cwd=source, check=True, capture_output=True)
        target = self.home / "stick"
        target.mkdir()
        self.mountinfo.write_text(self.mountinfo.read_text() + f"40 35 8:60 / {target} rw - {fstype} {self.base / 'dev' / 'sdb1'} rw\n")
        code, out = self.run_cli(
            "make-drive", str(target), "--source", str(source), "--uuid", "ABCD-1234", "--no-install", "--no-data", "--yes",
            "--pairing", str(self.pairing_path),
        )
        self.assertEqual(code, 0, out)
        root = target / "BabyAI"
        for path in root.rglob("*"):
            if path.is_file() and ".git" not in path.parts:
                path.chmod(0o755)  # what a FAT mount (fmask=0022) makes of every file
        return root

    def changed(self, root: Path, *flags: str) -> list[str]:
        done = subprocess.run(
            ["git", "-c", f"safe.directory={root}", *flags, "diff", "--name-only"], cwd=root, capture_output=True, text=True, check=True,
        )
        return sorted(name for name in done.stdout.split() if name != "config.toml")  # (the portable settings really are an edit)

    def test_on_fat_git_does_not_see_the_exec_bits_as_edits(self) -> None:
        root = self.make_stick("vfat")
        self.assertEqual(self.changed(root), [])
        self.assertIn("babyai/main.py", self.changed(root, "-c", "core.fileMode=true"), "this test would not notice the problem")

    def test_on_a_linux_filesystem_git_keeps_noticing_mode_changes(self) -> None:
        root = self.make_stick("ext4")
        self.assertIn("babyai/main.py", self.changed(root))

    def test_the_trust_manifest_is_taken_after_the_setting_so_it_includes_it(self) -> None:
        root = self.make_stick("vfat")
        saved = usbwatch.load_pairing(self.pairing_path)
        self.assertEqual(usbwatch.trust_changes(saved, root), [])
        self.assertIn(".git/config", saved["trust"])


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------------------------- the display after login


class ManagerEnvironmentTests(Disk):
    """What the user's systemd manager says, with a fake ``systemctl`` first on PATH."""

    def fake_systemctl(self, output: str, *, code: int = 0, hang: bool = False) -> None:
        bin_dir = self.home / "bin"
        bin_dir.mkdir(exist_ok=True)
        (self.home / "systemctl.out").write_text(output)
        tool = bin_dir / "systemctl"
        tool.write_text(
            f'#!/bin/sh\necho "$@" >> "{self.home / "systemctl.calls"}"\n'
            + ("sleep 5\n" if hang else "")
            + f'cat "{self.home / "systemctl.out"}"\nexit {code}\n'
        )
        tool.chmod(0o755)
        patch = mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}/usr/bin:/bin"})
        patch.start()
        self.addCleanup(patch.stop)

    def test_only_the_three_display_names_are_taken(self) -> None:
        self.fake_systemctl(
            "DISPLAY=:0\nWAYLAND_DISPLAY=wayland-0\nXAUTHORITY=/run/user/1000/xauth_x\nSECRET_TOKEN=hunter2\nPATH=/usr/bin\nXAUTHORITY2=/x\n"
        )
        self.assertEqual(
            REAL_MANAGER_ENVIRONMENT(),
            {"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0", "XAUTHORITY": "/run/user/1000/xauth_x"},
        )
        self.assertEqual((self.home / "systemctl.calls").read_text().strip(), "--user show-environment")

    def test_a_value_systemd_had_to_quote_is_not_used(self) -> None:
        self.fake_systemctl("XAUTHORITY=$'/odd\\npath'\nDISPLAY=:1\n")
        self.assertEqual(REAL_MANAGER_ENVIRONMENT(), {"DISPLAY": ":1"})

    def test_a_manager_that_fails_is_missing_or_hangs_gives_nothing(self) -> None:
        self.fake_systemctl("DISPLAY=:0\n", code=1)
        self.assertEqual(REAL_MANAGER_ENVIRONMENT(), {})
        self.fake_systemctl("DISPLAY=:0\n", hang=True)
        self.assertEqual(REAL_MANAGER_ENVIRONMENT(timeout=0.2), {})
        with mock.patch.dict(os.environ, {"PATH": str(self.home / "empty")}):
            self.assertEqual(REAL_MANAGER_ENVIRONMENT(), {})


class SessionDisplayEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.sockets = self.dir / "x11"
        self.sockets.mkdir()
        (self.sockets / "X0").write_text("")
        self.old = str(self.dir / "xauth_old")  # (never created: Xwayland took it away when it stopped)
        self.new = self.dir / "xauth_new"
        self.new.write_text("")

    def resolve(self, live: dict[str, str], inherited: dict[str, str]) -> dict[str, str | None]:
        return usbwatch.session_display_env({"XDG_RUNTIME_DIR": str(self.dir), **inherited}, lambda: live, self.sockets)

    def test_the_sessions_current_values_win_over_the_ones_the_watcher_started_with(self) -> None:
        got = self.resolve({"DISPLAY": ":0", "XAUTHORITY": str(self.new)}, {"DISPLAY": ":0", "XAUTHORITY": self.old})
        self.assertEqual((got["DISPLAY"], got["XAUTHORITY"]), (":0", str(self.new)))

    def test_when_both_still_exist_the_session_is_believed_over_the_watcher(self) -> None:
        lingering = self.dir / "xauth_lingering"
        lingering.write_text("")
        (self.sockets / "X1").write_text("")
        got = self.resolve({"DISPLAY": ":1", "XAUTHORITY": str(self.new)}, {"DISPLAY": ":0", "XAUTHORITY": str(lingering)})
        self.assertEqual((got["DISPLAY"], got["XAUTHORITY"]), (":1", str(self.new)))

    def test_a_stale_authority_file_is_dropped_when_nothing_better_exists(self) -> None:
        got = self.resolve({}, {"DISPLAY": ":0", "XAUTHORITY": self.old})
        self.assertIsNone(got["XAUTHORITY"])
        self.assertEqual(got["DISPLAY"], ":0")

    def test_what_was_inherited_is_used_when_the_session_has_nothing_to_say(self) -> None:
        got = self.resolve({}, {"DISPLAY": ":0", "XAUTHORITY": str(self.new), "WAYLAND_DISPLAY": "wayland-0"})
        self.assertEqual((got["DISPLAY"], got["XAUTHORITY"], got["WAYLAND_DISPLAY"]), (":0", str(self.new), "wayland-0"))

    def test_a_current_value_that_points_at_nothing_does_not_beat_one_that_works(self) -> None:
        got = self.resolve({"DISPLAY": ":9", "XAUTHORITY": self.old}, {"DISPLAY": ":0", "XAUTHORITY": str(self.new)})
        self.assertEqual((got["DISPLAY"], got["XAUTHORITY"]), (":0", str(self.new)))

    def test_a_display_that_cannot_be_checked_is_kept_rather_than_thrown_away(self) -> None:
        self.assertEqual(self.resolve({}, {"DISPLAY": "localhost:10.0"})["DISPLAY"], "localhost:10.0")
        self.assertEqual(self.resolve({}, {"DISPLAY": ":7"})["DISPLAY"], ":7")  # (no socket to see, but it is all there is)

    def test_the_wayland_socket_is_looked_for_in_the_runtime_folder(self) -> None:
        (self.dir / "wayland-1").write_text("")
        got = self.resolve({"WAYLAND_DISPLAY": "wayland-0"}, {"WAYLAND_DISPLAY": "wayland-1"})
        self.assertEqual(got["WAYLAND_DISPLAY"], "wayland-1")

    def test_nothing_is_made_up_when_there_is_nothing(self) -> None:
        self.assertEqual(self.resolve({}, {}), {"DISPLAY": None, "WAYLAND_DISPLAY": None, "XAUTHORITY": None})

    def test_the_authority_file_is_never_read(self) -> None:
        self.new.write_text("SECRETCOOKIE")
        with mock.patch("builtins.open", side_effect=AssertionError("opened a file")):
            got = self.resolve({"XAUTHORITY": str(self.new)}, {})
        self.assertEqual(got["XAUTHORITY"], str(self.new))
        self.assertNotIn("SECRETCOOKIE", usbwatch.display_report(got))


class WaitForDisplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []
        self.said: list[str] = []

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def wait(self, answers: list[dict[str, str | None]], **kwargs: object) -> dict[str, str | None]:
        feed = iter(answers)
        last = answers[-1]
        return usbwatch.wait_for_display(
            self.said.append, resolve=lambda: next(feed, last), sleep=self.sleep, clock=lambda: self.now, **kwargs  # type: ignore[arg-type]
        )

    def test_a_desktop_that_is_there_is_not_waited_for(self) -> None:
        got = self.wait([{"DISPLAY": ":0"}])
        self.assertEqual(got["DISPLAY"], ":0")
        self.assertEqual(self.slept, [])

    def test_it_waits_until_the_desktop_appears_and_says_so_once(self) -> None:
        got = self.wait([{}, {}, {}, {"DISPLAY": ":0"}])
        self.assertEqual(got["DISPLAY"], ":0")
        self.assertEqual(len(self.slept), 3)
        self.assertEqual(sum("isn't ready yet" in line for line in self.said), 1)

    def test_it_gives_up_after_the_timeout_and_explains(self) -> None:
        with self.assertRaisesRegex(usbwatch.LaunchError, "no display"):
            self.wait([{}], timeout=5.0)
        self.assertEqual(sum(self.slept), 5.0)

    def test_a_wayland_display_without_an_x_display_starts_him_anyway_and_says_so(self) -> None:
        got = self.wait([{"WAYLAND_DISPLAY": "wayland-0"}], timeout=2.0)
        self.assertEqual(got["WAYLAND_DISPLAY"], "wayland-0")
        self.assertTrue(any("no X display" in line for line in self.said))

    def test_stopping_the_watcher_ends_the_wait_at_once(self) -> None:
        with self.assertRaisesRegex(usbwatch.LaunchError, "no display"):
            self.wait([{}], cancelled=lambda: True)
        self.assertEqual(self.slept, [])


REAL_MANAGER_ENVIRONMENT = usbwatch._manager_environment
