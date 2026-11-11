"""The USB launcher (babyai/usbwatch.py): the drive is a key that starts him when it is plugged in and closes him when it is taken out.

There is no real USB drive here. /dev/disk is imitated with a folder of symlinks (that is all the watcher looks at), the
watching is driven one step at a time with a fake clock, and the starting and closing is also tried on real processes.
Nothing here touches the real ~/.config, ~/.local/state or /dev."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
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
            mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.home / "state"), "XDG_CONFIG_HOME": str(self.home / "cfg")}),
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

    def test_the_login_item_runs_the_watcher_from_this_checkout_with_this_config(self) -> None:
        code, _ = self.run_cli("install", "--config", str(self.config))
        self.assertEqual(code, 0)
        text = usbwatch.autostart_path().read_text()
        self.assertIn("[Desktop Entry]\nType=Application\n", text)
        self.assertIn("Terminal=false\n", text)
        self.assertIn(f"Path={ROOT}\n", text)
        exec_line = next(line for line in text.splitlines() if line.startswith("Exec="))[len("Exec="):]
        self.assertEqual(shlex.split(exec_line), ["env", f"PYTHONPATH={ROOT}", sys.executable, "-m", "babyai.usbwatch", "run", "--config", str(self.config.resolve())])

    def test_what_the_login_item_runs_is_something_the_command_line_accepts(self) -> None:
        self.run_cli("install", "--config", str(self.config))
        exec_line = next(l for l in usbwatch.autostart_path().read_text().splitlines() if l.startswith("Exec="))[len("Exec="):]
        argv = shlex.split(exec_line)
        args = usbwatch.build_parser().parse_args(argv[argv.index("babyai.usbwatch") + 1:])
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
        self.assertEqual(shlex.split(exec_line)[-1], str(spaced.resolve()))
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
        with mock.patch.object(usbwatch, "spawn") as spawn:
            watcher._launch_fn()
        command, cwd, directory = spawn.call_args.args
        self.assertEqual(command, [sys.executable, "-m", "babyai", "--desktop", "--config", str(self.config.resolve())])
        self.assertEqual(cwd, ROOT)
        self.assertEqual(directory, self.home / "state" / "babyai")

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


if __name__ == "__main__":
    unittest.main()
