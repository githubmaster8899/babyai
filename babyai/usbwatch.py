"""Start him when a certain USB drive is plugged in, and close him when it is taken out. (Linux; it reads /dev/disk.)

Set it up once::

    python3 -m babyai.usbwatch pair       # plug the drive in first; pick it from the list

``pair`` remembers the drive in ``[usb] drive`` of your config.toml and installs a small login item (an XDG autostart entry,
which KDE shows under System Settings -> Autostart) that runs ``python3 -m babyai.usbwatch run``. That watcher looks once a
second for the drive's name under /dev/disk, starts ``python3 -m babyai --desktop`` when it shows up, and stops that same
process when the drive has been gone for ``grace_seconds``.

The drive is only a key: nothing on it is read, mounted or run. The watcher only ever closes the process it started itself.
If you close him by hand while the drive is still in, he stays closed until the drive is taken out and plugged in again.

Other commands: ``list`` (the USB drives plugged in now), ``status``, ``run`` (in the foreground, to see what it does),
``install`` / ``uninstall`` (the login item).
"""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from babyai.config.loader import load_config

DISK = Path("/dev/disk")
SYS_BLOCK = Path("/sys/block")
KINDS = {"uuid": "by-uuid", "label": "by-label", "id": "by-id"}
POLL_SECONDS = 1.0
STOP_WAIT_SECONDS = 5.0  # how long he gets to close politely before he is made to
MAX_LOG_BYTES = 1_000_000
SAFE_PATH = re.compile(r"[A-Za-z0-9_@+=:,./ -]+")  # what may go into an autostart Exec line without any quoting rules to get wrong


# ---------------------------------------------------------------------------------------------- which drive


def parse_spec(text: str) -> tuple[str, str]:
    """``uuid:ABCD-1234`` / ``label:MYSTICK`` / ``id:usb-SanDisk_...-0:0`` -> (kind, name). The name is a file name under /dev/disk."""
    kind, _, name = (text or "").strip().partition(":")
    if kind not in KINDS or not name or len(name) > 200 or "/" in name or "\0" in name or name in (".", ".."):
        raise ValueError(f"{text!r} isn't a drive I can look for. It should look like uuid:ABCD-1234 (run: python3 -m babyai.usbwatch list).")
    return kind, name


def is_present(spec: str, disk: Path | None = None) -> bool:
    kind, name = parse_spec(spec)
    return os.path.exists((disk or DISK) / KINDS[kind] / name)  # (exists() follows the link: a link to a device that is gone does not count)


def _unescape(name: str) -> str:
    return re.sub(r"\\x([0-9a-fA-F]{2})", lambda match: chr(int(match.group(1), 16)), name)


@dataclass
class Drive:
    ident: str  # the by-id name of the whole drive
    name: str  # something a person can read
    device: str  # /dev/sdb
    size: str
    specs: list[str] = field(default_factory=list)  # ways to recognise it, best first


def _size(device: str, sys_block: Path) -> str:
    try:
        sectors = int((sys_block / Path(device).name / "size").read_text())
    except (OSError, ValueError):
        return "?"
    gigabytes = sectors * 512 / 1e9
    return f"{gigabytes:.1f} GB" if gigabytes >= 1 else f"{gigabytes * 1000:.0f} MB"


def drives(disk: Path | None = None, sys_block: Path | None = None) -> list[Drive]:
    """The USB drives plugged in right now, with the ways they can be recognised."""
    disk, sys_block = disk or DISK, sys_block or SYS_BLOCK
    found: list[Drive] = []
    by_id = disk / "by-id"
    try:
        entries = sorted(by_id.iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.name.startswith("usb-") or re.search(r"-part\d+$", entry.name):
            continue
        device = os.path.realpath(entry)
        if not os.path.exists(device):
            continue
        specs: dict[str, list[str]] = {"uuid": [], "label": []}
        for kind in ("uuid", "label"):
            try:
                links = sorted((disk / KINDS[kind]).iterdir())
            except OSError:
                continue
            for link in links:
                if re.fullmatch(re.escape(device) + r"p?\d*", os.path.realpath(link)):  # the drive itself or one of its partitions
                    specs[kind].append(f"{kind}:{link.name}")
        pretty = _unescape(re.sub(r"-\d+:\d+$", "", entry.name[len("usb-"):])).replace("_", " ")
        found.append(Drive(entry.name, pretty, device, _size(device, sys_block), specs["uuid"] + specs["label"] + [f"id:{entry.name}"]))
    return found


# ---------------------------------------------------------------------------------------------- the watching


class Watcher:
    """One poll at a time: ``step()``. Knows nothing about disks or processes except through what it is given, so it can be tested."""

    def __init__(
        self, present: Callable[[], bool], launch: Callable[[], Any], stop: Callable[[Any], None], *,
        elsewhere: Callable[[], bool] = lambda: False, clock: Callable[[], float] = time.monotonic, grace: float = 3.0,
        launch_if_present: bool = True, close_on_remove: bool = True, log: Callable[[str], None] = lambda message: None,
    ) -> None:
        self._present, self._launch_fn, self._stop_fn, self._elsewhere = present, launch, stop, elsewhere
        self._clock, self.grace, self.launch_if_present, self.close_on_remove, self._log = clock, grace, launch_if_present, close_on_remove, log
        self.child: Any = None  # the one process this watcher started
        self._plugged = False
        self._gone_since: float | None = None
        self._first = True

    def step(self) -> None:
        now = self._clock()
        if self._present():
            self._gone_since = None
            if not self._plugged:
                self._plugged = True
                at_start, self._first = self._first, False
                if at_start and not self.launch_if_present:
                    self._log("The drive was already in when I started, and launch_if_present is off: not starting him.")
                else:
                    self._launch()
            return
        self._first = False
        if not self._plugged:
            return
        if self._gone_since is None:
            self._gone_since = now
        if now - self._gone_since >= self.grace:
            self._plugged, self._gone_since = False, None
            self._close()

    def _alive(self) -> bool:
        return self.child is not None and self.child.poll() is None

    def _launch(self) -> None:
        if self._alive():
            return
        if self._elsewhere():
            self._log("The drive is in, but he is already running (started by hand?), so I'm not starting a second one. I won't close that one either.")
            return
        try:
            self.child = self._launch_fn()
        except Exception as exc:  # a launch that fails is reported once, not retried in a loop: it is tried again at the next plug-in
            self._log(f"Could not start him: {exc}")
            self.child = None
            return
        self._log("The drive is in: started him.")

    def _close(self) -> None:
        child = self.child
        if child is None or child.poll() is not None:
            self.child = None
            self._log("The drive is out.")
            return
        if not self.close_on_remove:  # (he stays his: a second one is not started at the next plug-in while this one is up)
            self._log("The drive is out; close_on_remove is off, so I'm leaving him running.")
            return
        try:
            self._stop_fn(child)
        except Exception as exc:  # (still his, too: the next plug-in must not start another beside it)
            self._log(f"Could not close him: {exc}")
            return
        self.child = None
        self._log("The drive is out: closed him.")


def stop_process(process: subprocess.Popen, wait: float = STOP_WAIT_SECONDS, poll: float = 0.1) -> None:
    """Ask politely (SIGTERM), then insist (SIGKILL). He was started in a group of his own, so anything he started goes too."""
    try:
        own_group = os.getpgid(process.pid) == process.pid
    except ProcessLookupError:  # already gone
        process.poll()
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig) if own_group else process.send_signal(sig)
        except ProcessLookupError:
            break
        deadline = time.monotonic() + (wait if sig == signal.SIGTERM else 2.0)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return
            time.sleep(poll)
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        pass


def another_babyai_running(proc: Path = Path("/proc"), me: int | None = None) -> bool:
    """Is some other `python -m babyai --desktop` of this user already running?"""
    me = os.getpid() if me is None else me
    try:
        entries = list(proc.iterdir())
    except OSError:
        return False
    for entry in entries:
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
            if entry.stat().st_uid != os.getuid():
                continue
        except OSError:
            continue
        if b"--desktop" in argv and any(a == b"-m" and b == b"babyai" for a, b in zip(argv, argv[1:])):
            return True
    return False


# ---------------------------------------------------------------------------------------------- files


def state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or "~/.local/state").expanduser() / "babyai"


def autostart_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / "autostart" / "babyai-usb.desktop"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def launch_command(config: Path) -> list[str]:
    return [sys.executable, "-m", "babyai", "--desktop", "--config", str(config)]


def _log_to_file(directory: Path) -> Callable[[str], None]:
    path = directory / "usbwatch.log"

    def write(message: str) -> None:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
                path.write_text("")
            with path.open("a") as handle:
                handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
        except OSError:
            pass
        print(message, flush=True)

    return write


def spawn(command: list[str], cwd: Path, directory: Path) -> subprocess.Popen:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "babyai-from-usb.log"  # what he prints: the only place a failed start shows up
    output = open(path, "wb" if path.exists() and path.stat().st_size > MAX_LOG_BYTES else "ab")
    try:
        return subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True)
    finally:
        output.close()


def take_lock(directory: Path) -> Any:
    """One watcher at a time. Returns the open lock file (keep it open), or None if another watcher holds it."""
    directory.mkdir(parents=True, exist_ok=True)
    handle = open(directory / "usbwatch.pid", "a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    return handle


def running_watcher(directory: Path) -> int | None:
    """The pid of a running watcher, if there is one (checked against the process's own command line, so a reused pid isn't mistaken)."""
    try:
        pid = int((directory / "usbwatch.pid").read_text().strip())
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except (OSError, ValueError):
        return None
    return pid if b"babyai.usbwatch" in argv else None


def watch(watcher: Watcher, done: threading.Event, poll: float = POLL_SECONDS, log: Callable[[str], None] = lambda message: None) -> None:
    """Step until ``done`` is set. A step that goes wrong is reported (once, until it is a different problem) and the watching goes on."""
    last = ""
    while not done.is_set():
        try:
            watcher.step()
            last = ""
        except Exception as exc:
            if str(exc) != last:
                log(f"Something went wrong while watching: {exc}")
            last = str(exc)
        done.wait(poll)


def stop_watcher(directory: Path, wait: float = 3.0) -> bool:
    pid = running_watcher(directory)
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline and running_watcher(directory) == pid:
        time.sleep(0.05)
    return True


def write_setting(config: Path, table: str, key: str, value: str) -> None:
    """Put ``key = "value"`` into ``[table]`` of the config (keeping a .bak of what was there the first time)."""
    from babyai.upgrade.models import set_toml_value

    old = config.read_text() if config.exists() else ""
    backup = config.with_name(config.name + ".bak")
    if old and not backup.exists():
        backup.write_text(old)
    temp = config.with_name(config.name + ".tmp")
    temp.write_text(set_toml_value(old, table, key, value))
    os.replace(temp, config)


def _exec_arg(text: str) -> str:
    if not SAFE_PATH.fullmatch(text):
        raise ValueError(f"{text!r} has characters I can't safely put in a login item. Move it somewhere with a plainer path.")
    return f'"{text}"' if " " in text else text


def desktop_entry(python: str, root: Path, config: Path) -> str:
    exec_line = " ".join(["env", _exec_arg(f"PYTHONPATH={root}"), _exec_arg(python), "-m", "babyai.usbwatch", "run", "--config", _exec_arg(str(config))])
    return "\n".join([
        "[Desktop Entry]", "Type=Application", "Name=BabyAI USB launcher",
        "Comment=Starts BabyAI when your paired USB drive is plugged in and closes it when the drive is taken out",
        f"Exec={exec_line}", f"Path={root}", "Terminal=false", "X-GNOME-Autostart-enabled=true", "",
    ])


# ---------------------------------------------------------------------------------------------- commands


def read_spec(config: Path) -> str:
    return str(load_config(str(config)).get("usb", {}).get("drive", "") or "").strip()


def cmd_list(args: argparse.Namespace) -> int:
    found = drives()
    if not found:
        print("No USB drives found. Plug it in and run this again. (This reads /dev/disk, so it only works on Linux.)")
        return 1
    for number, drive in enumerate(found, 1):
        print(f"{number}) {drive.name}  {drive.size}  ({drive.device})")
        for spec in drive.specs:
            print(f"      {spec}")
    return 0


def _paired(config: Path) -> str | None:
    """The drive in the config, or None (after saying what is wrong)."""
    spec = read_spec(config)
    if not spec:
        print(f"No drive is paired yet. Run: {sys.executable} -m babyai.usbwatch pair")
        return None
    try:
        parse_spec(spec)
    except ValueError as exc:
        print(exc)
        return None
    return spec


def cmd_run(args: argparse.Namespace) -> int:
    config = Path(os.path.expanduser(args.config)).resolve()
    spec = _paired(config)
    if spec is None:
        return 2
    settings = load_config(str(config))["usb"]
    directory = state_dir()
    lock = take_lock(directory)
    if lock is None:
        print("The USB watcher is already running.")
        return 0
    log = _log_to_file(directory)
    log(f"Watching for {spec} (config {config}).")
    watcher = Watcher(
        lambda: is_present(spec), lambda: spawn(launch_command(config), repo_root(), directory), stop_process,
        elsewhere=another_babyai_running, grace=float(settings.get("grace_seconds", 3)),
        launch_if_present=bool(settings.get("launch_if_present", True)), close_on_remove=bool(settings.get("close_on_remove", True)), log=log,
    )
    done = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: done.set())
    try:
        watch(watcher, done, POLL_SECONDS, log)
    finally:
        lock.close()
    log("Stopped watching. (If he is running he stays running.)")
    return 0


def start_watcher(config: Path) -> None:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / "usbwatch-run.log", "ab") as output:
        subprocess.Popen(
            [sys.executable, "-m", "babyai.usbwatch", "run", "--config", str(config)], cwd=repo_root(), stdin=subprocess.DEVNULL,
            stdout=output, stderr=output, start_new_session=True,
        )


def cmd_install(args: argparse.Namespace) -> int:
    config = Path(os.path.expanduser(args.config)).resolve()
    if _paired(config) is None:
        return 2
    try:
        entry = desktop_entry(sys.executable, repo_root(), config)
    except ValueError as exc:
        print(exc)
        return 2
    path = autostart_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(entry)
    stop_watcher(state_dir())  # (a watcher already running has the old settings)
    start_watcher(config)
    print(f"Installed: {path}\nIt starts when you log in, and I've started it now.")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    path = autostart_path()
    removed = path.exists()
    path.unlink(missing_ok=True)
    stopped = stop_watcher(state_dir())
    print(("Removed the login item. " if removed else "There was no login item. ") + ("Stopped the watcher." if stopped else "The watcher wasn't running."))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = Path(os.path.expanduser(args.config)).resolve()
    spec = read_spec(config)
    print(f"Config: {config}")
    print(f"Paired drive: {spec or '(none: run pair)'}")
    if spec:
        try:
            print(f"Drive plugged in now: {'yes' if is_present(spec) else 'no'}")
        except ValueError as exc:
            print(exc)
    print(f"Login item: {'installed' if autostart_path().exists() else 'not installed'} ({autostart_path()})")
    pid = running_watcher(state_dir())
    print(f"Watcher: {'running, pid ' + str(pid) if pid else 'not running'}")
    log = state_dir() / "usbwatch.log"
    if log.exists():
        print("Last lines of the log:")
        for line in log.read_text().splitlines()[-5:]:
            print("  " + line)
    return 0


def _choose(found: list[Drive], pick: int | None) -> Drive | None:
    if pick is not None:
        return found[pick - 1] if 1 <= pick <= len(found) else None
    if len(found) == 1:
        return found[0]
    cmd_list(argparse.Namespace())
    try:
        answer = int(input("Which one is it? (number) ").strip())
    except (ValueError, EOFError):
        return None
    return found[answer - 1] if 1 <= answer <= len(found) else None


def cmd_pair(args: argparse.Namespace) -> int:
    config = Path(os.path.expanduser(args.config)).resolve()
    if args.spec:
        try:
            parse_spec(args.spec)
        except ValueError as exc:
            print(exc)
            return 2
        spec = args.spec.strip()
    else:
        found = drives()
        if not found:
            print("I can't see a USB drive. Plug the one you want in and run this again. (This reads /dev/disk, so it only works on Linux.)")
            return 1
        drive = _choose(found, args.pick)
        if drive is None:
            print("No drive chosen.")
            return 1
        wanted = args.by or ("uuid" if any(s.startswith("uuid:") for s in drive.specs) else "label" if any(s.startswith("label:") for s in drive.specs) else "id")
        spec = next((s for s in drive.specs if s.startswith(wanted + ":")), None)
        if spec is None:
            print(f"That drive has no {wanted}. What I can use for it: {', '.join(drive.specs)}")
            return 1
        print(f"Using {drive.name} ({drive.size}), recognised by {spec}.")
        if wanted == "uuid":
            print("(That's its filesystem's id: it stays the same in any USB port but changes if you reformat the drive. Re-run pair then.)")
    write_setting(config, "usb", "drive", spec)
    print(f"Saved to {config}: [usb] drive = {spec!r}")
    if args.no_install:
        print("Not installing the login item (--no-install). Run `python3 -m babyai.usbwatch run` to try it in this window.")
        return 0
    return cmd_install(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m babyai.usbwatch", description="Start him when your USB drive is plugged in and close him when it is taken out.")
    common = argparse.ArgumentParser(add_help=False)  # (after the command: `... pair --config ~/babyai/config.toml`)
    common.add_argument("--config", default="config.toml", help="Path to config.toml (default: the one in the folder you run this from, like babyai itself)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", parents=[common], help="show the USB drives plugged in now").set_defaults(run=cmd_list)
    pair = sub.add_parser("pair", parents=[common], help="choose the drive, remember it, and install the login item")
    pair.add_argument("--spec", help="skip the list: uuid:..., label:... or id:... exactly as `list` prints it")
    pair.add_argument("--pick", type=int, help="the number from `list`")
    pair.add_argument("--by", choices=sorted(KINDS), help="recognise the drive by its filesystem id, its label, or the hardware itself")
    pair.add_argument("--no-install", action="store_true", help="only remember the drive")
    pair.set_defaults(run=cmd_pair)
    sub.add_parser("run", parents=[common], help="watch in the foreground (what the login item runs)").set_defaults(run=cmd_run)
    sub.add_parser("install", parents=[common], help="install the login item and start the watcher now").set_defaults(run=cmd_install)
    sub.add_parser("uninstall", parents=[common], help="remove the login item and stop the watcher").set_defaults(run=cmd_uninstall)
    sub.add_parser("status", parents=[common], help="what is set up, and what the watcher has been doing").set_defaults(run=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
