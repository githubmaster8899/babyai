"""Start him when a certain USB drive is plugged in, and close him when it is taken out. (Linux; it reads /dev/disk.)

This file is deliberately self-contained: the copy installed in
``~/.local/share/babyai-usb/usbwatch.py`` has to run without the repository or
the ``babyai`` package.  Keep it to the standard library.

Two modes:

``key`` (the old behaviour)
    The drive is only a key.  Nothing on it is read, mounted or run.  Plugging
    it in starts ``python3 -m babyai --desktop --config ...`` from the checkout,
    and taking it out closes that same process after ``grace_seconds``.

``portable``
    The app, assets, config and data live on the drive under
    ``<mount>/<app_folder>``.  Pairing records the filesystem UUID, not a
    mount path, so the stick can use any USB port.  Before launching, every
    ``.py`` file in the app folder is checked against the SHA-256 manifest in
    ``~/.config/babyai-usb/pairing.toml``.  A changed file refuses launch and
    ``python3 -m babyai.usbwatch trust`` lists it and asks.

Set it up once::

    python3 -m babyai.usbwatch pair              # key mode
    python3 -m babyai.usbwatch make-drive /media/you/STICK   # portable mode

``make-drive`` copies the app, assets, config and data to the stick, writes the
portable config, records the trust manifest, installs this watcher as a login
item and starts it.  The drive's root must be a mounted filesystem; it refuses a
non-empty ``<mount>/<app_folder>`` without ``--force``, and it shows
``findmnt`` and asks before the first write.

Other commands: ``list`` (USB drives plugged in now), ``status``, ``run`` (in
the foreground), ``install`` / ``uninstall``, ``trust`` (the only way to accept
edited launcher/app files), ``eject`` (close this app and unmount the drive).
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ is required
    tomllib = None  # type: ignore[assignment]

DISK = Path("/dev/disk")
SYS_BLOCK = Path("/sys/block")
MOUNTINFO = Path("/proc/self/mountinfo")
KINDS = {"uuid": "by-uuid", "label": "by-label", "id": "by-id"}
POLL_SECONDS = 1.0
STOP_WAIT_SECONDS = 5.0  # how long he gets to close politely before he is made to
MAX_LOG_BYTES = 1_000_000
SAFE_PATH = re.compile(r"[A-Za-z0-9_@+=:,./ -]+")  # what may go into an autostart Exec line without quoting rules to get wrong
# What the manifest covers (the same rules as babyai/portable.py, which has to be kept in step: tests compare the two).
SKIP_DIRS = frozenset({".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})  # (anywhere in the tree)
TOP_SKIPPED = frozenset({"data", "runtime", "ollama"})  # (only at the top: his data, and the bundles covered by TREES)
TREES = ("runtime", "ollama/bin")  # bundled programs: if they are on the stick they run as him, so they are in the manifest
GIT_FILES = (".git/config", ".git/info/attributes")  # what git runs on its own: config (fsmonitor, hooks path...) and hooks
PAIRING_NAME = "pairing.toml"
DISPLAY_KEYS = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY")  # what a window needs; they change from one login (or Xwayland restart) to the next
X11_SOCKETS = Path("/tmp/.X11-unix")
DISPLAY_WAIT_SECONDS = 45.0  # how long to wait for the desktop after login before giving up on a launch


class LaunchError(Exception):
    """A launch that should be explained to the user, not retried every second."""


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


def device_for(spec: str, disk: Path | None = None) -> Path | None:
    """The device node a spec points at, or None if it is not plugged in."""
    kind, name = parse_spec(spec)
    link = (disk or DISK) / KINDS[kind] / name
    try:
        if not link.exists():
            return None
        return Path(os.path.realpath(link))
    except OSError:
        return None


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


# ---------------------------------------------------------------------------------------------- mounts


def _mountinfo_unescape(text: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), text)


@dataclass
class Mount:
    target: Path
    source: str
    fstype: str
    options: str
    major_minor: str


def mounts(path: Path | None = None) -> list[Mount]:
    """Parse /proc/self/mountinfo, including its octal escapes for spaces."""
    try:
        lines = (path or MOUNTINFO).read_text(errors="replace").splitlines()
    except OSError:
        return []
    found: list[Mount] = []
    for line in lines:
        left, _, right = line.partition(" - ")
        fields = left.split()
        after = right.split()
        if len(fields) < 6 or len(after) < 2:
            continue
        found.append(Mount(
            target=Path(_mountinfo_unescape(fields[4])),
            source=_mountinfo_unescape(after[1]),
            fstype=after[0],
            options=f"{fields[5]},{after[2] if len(after) > 2 else ''}".strip(","),
            major_minor=fields[2],
        ))
    return found


def mount_point(device: str | Path, path: Path | None = None) -> Path | None:
    """Where this device is mounted, matching the real device path, not just its name."""
    wanted = os.path.realpath(str(device))
    for item in mounts(path):
        source = item.source
        if source.startswith("/dev/") and os.path.realpath(source) == wanted:
            if item.target.is_dir():
                return item.target
        elif source == str(device) and item.target.is_dir():
            return item.target
    return None


def mount_for_path(path: str | Path, path_file: Path | None = None) -> Mount | None:
    wanted = Path(path).expanduser().resolve()
    for item in mounts(path_file):
        try:
            if item.target.resolve() == wanted:
                return item
        except OSError:
            continue
    return None


def wait_for_mount(device: str | Path, timeout: float = 20.0, log: Callable[[str], None] = lambda message: None) -> Path | None:
    """KDE mounts a stick a moment after the device appears; give it time, then try udisksctl."""
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        found = mount_point(device)
        if found is not None:
            return found
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    udisksctl = shutil.which("udisksctl")
    if udisksctl:
        log(f"It isn't mounted yet; asking udisksctl to mount {device}.")
        try:
            subprocess.run([udisksctl, "mount", "-b", str(device)], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log(f"udisksctl did not mount it: {exc}")
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            found = mount_point(device)
            if found is not None:
                return found
            time.sleep(0.5)
    return None


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


def stop_pid(pid: int, wait: float = STOP_WAIT_SECONDS) -> None:
    """Stop a process group we verified, without needing its Popen object (used by eject)."""
    try:
        own_group = os.getpgid(pid) == pid
    except ProcessLookupError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig) if own_group else os.kill(pid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + (wait if sig == signal.SIGTERM else 2.0)
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)


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


def pairing_path() -> Path:
    override = os.environ.get("BABYAI_PAIRING")
    if override:
        return Path(override).expanduser().resolve()
    return Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / "babyai-usb" / PAIRING_NAME


def stub_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or "~/.local/share").expanduser() / "babyai-usb"


def stub_path() -> Path:
    return stub_dir() / "usbwatch.py"


def repo_root() -> Path:
    here = Path(__file__).resolve()
    return here.parents[1]


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


def spawn(command: list[str], cwd: Path, directory: Path, env: dict[str, str | None] | None = None) -> subprocess.Popen:
    """Start ``command`` in a session of its own.  ``env`` is added to this process's environment; a value of ``None`` removes that variable."""
    directory.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    for name, value in (env or {}).items():
        if value is None:
            environment.pop(name, None)
        else:
            environment[name] = value
    path = directory / "babyai-from-usb.log"  # what he prints: the only place a failed start shows up
    output = open(path, "wb" if path.exists() and path.stat().st_size > MAX_LOG_BYTES else "ab")
    try:
        return subprocess.Popen(
            command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True, env=environment,
        )
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
    looks_like = any(b"babyai.usbwatch" in part or part.endswith(b"usbwatch.py") for part in argv)
    return pid if looks_like else None


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


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _toml_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value))


def _toml_dump(data: dict[str, Any]) -> str:
    lines: list[str] = []
    tables: list[tuple[str, dict[str, Any]]] = []
    for key, value in data.items():
        if isinstance(value, dict):
            tables.append((str(key), value))
        else:
            lines.append(f"{_toml_key(str(key))} = {_toml_value(value)}")
    for key, table in tables:
        lines.append("")
        lines.append(f"[{_toml_key(key)}]")
        for name, value in table.items():
            if not isinstance(value, dict):
                lines.append(f"{_toml_key(str(name))} = {_toml_value(value)}")
    return "\n".join(lines).rstrip() + "\n"


def load_pairing(path: Path | None = None) -> dict[str, Any]:
    target = path or pairing_path()
    if tomllib is None:
        return {}
    try:
        with open(target, "rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def save_pairing(data: dict[str, Any], path: Path | None = None) -> None:
    _write_text_atomic(path or pairing_path(), _toml_dump(data))


def _config_data(path: Path) -> dict[str, Any]:
    if tomllib is None:
        return {}
    try:
        with open(path, "rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _set_toml_raw(text: str, table: str, key: str, rendered: str) -> str:
    line = f"{key} = {rendered}"
    lines = text.splitlines()
    header = re.compile(rf"^\s*\[{re.escape(table)}\]\s*(#.*)?$")
    any_header = re.compile(r"^\s*\[")
    start = next((i for i, current in enumerate(lines) if header.match(current)), None)
    if start is None:
        return text.rstrip("\n") + ("\n\n" if text.strip() else "") + f"[{table}]\n{line}\n"
    end = next((i for i in range(start + 1, len(lines)) if any_header.match(lines[i])), len(lines))
    for i in range(start + 1, end):
        if re.match(rf"^\s*{re.escape(key)}\s*=", lines[i]):
            lines[i] = line
            break
    else:
        lines.insert(start + 1, line)
    return "\n".join(lines) + "\n"


def set_toml_value(text: str, table: str, key: str, value: str) -> str:
    return _set_toml_raw(text, table, key, json.dumps(value))


def set_toml_bool(text: str, table: str, key: str, value: bool) -> str:
    return _set_toml_raw(text, table, key, "true" if value else "false")


def write_setting(config: Path, table: str, key: str, value: str) -> None:
    """Put ``key = "value"`` into ``[table]`` of the config (keeping a .bak of what was there the first time)."""
    old = config.read_text() if config.exists() else ""
    backup = config.with_name(config.name + ".bak")
    if old and not backup.exists():
        backup.write_text(old)
    _write_text_atomic(config, set_toml_value(old, table, key, value))


def _exec_arg(text: str) -> str:
    if not SAFE_PATH.fullmatch(text):
        raise ValueError(f"{text!r} has characters I can't safely put in a login item. Move it somewhere with a plainer path.")
    return f'"{text}"' if " " in text else text


def desktop_entry(python: str, stub: Path, config: Path | None = None, pairing: Path | None = None) -> str:
    parts = ["env", f"PYTHONDONTWRITEBYTECODE=1", _exec_arg(python), _exec_arg(str(stub)), "run"]
    if config is not None:
        parts += ["--config", _exec_arg(str(config))]
    if pairing is not None:
        parts += ["--pairing", _exec_arg(str(pairing))]
    return "\n".join([
        "[Desktop Entry]", "Type=Application", "Name=BabyAI USB launcher",
        "Comment=Starts BabyAI when your paired USB drive is plugged in and closes it when the drive is taken out",
        f"Exec={' '.join(parts)}", f"Path={_exec_arg(str(stub.parent))}", "Terminal=false", "X-GNOME-Autostart-enabled=true", "",
    ])


def install_stub() -> Path:
    """Install the one-file host watcher.  It imports nothing from the repository, so it runs on its own."""
    source = Path(__file__).resolve()
    target = stub_path().resolve()
    if source != target:
        target.parent.mkdir(parents=True, exist_ok=True)
        text = source.read_text(encoding="utf-8")
        _write_text_atomic(target, text)
    return target


def install_login_item(config: Path | None = None, pairing: Path | None = None) -> Path:
    stub = install_stub()
    entry = desktop_entry(sys.executable, stub, config, pairing)
    path = autostart_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(path, entry)
    return path


def start_watcher(config: Path | None = None, pairing: Path | None = None) -> None:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(stub_path()), "run"]
    if config is not None:
        command += ["--config", str(config)]
    if pairing is not None:
        command += ["--pairing", str(pairing)]
    with open(directory / "usbwatch-run.log", "ab") as output:
        subprocess.Popen(
            command, cwd=stub_dir(), stdin=subprocess.DEVNULL,
            stdout=output, stderr=output, start_new_session=True,
        )


# ---------------------------------------------------------------------------------------------- the portable side


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest(root: Path) -> dict[str, str]:
    """SHA-256 for everything on the stick that can run as him: every Python file, the git files that git would run
    (config and hooks), and, if they are there, the bundled Python and Ollama programs (one digest each for the tree)."""
    root = root.resolve()
    found: dict[str, str] = {}
    for folder, names, files in os.walk(root):
        here = Path(folder)
        top = here == root
        names[:] = sorted(name for name in names if name not in SKIP_DIRS and not (top and name in TOP_SKIPPED))
        for name in sorted(files):
            if name.endswith(".py"):
                _record(found, root, here / name)
    for name in GIT_FILES:
        _record(found, root, root / name)
    hooks = root / ".git" / "hooks"
    if hooks.is_dir():
        for path in sorted(hooks.iterdir()):
            if path.is_file() and not path.name.endswith(".sample"):
                _record(found, root, path)
    for tree in TREES:
        if (root / tree).is_dir():
            found[tree + "/"] = _tree_digest(root / tree)
    return found


def _record(found: dict[str, str], root: Path, path: Path) -> None:
    try:
        found[path.relative_to(root).as_posix()] = _sha256(path)
    except (OSError, ValueError):
        pass


def _tree_digest(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in folder.rglob("*") if item.is_file()):
        try:
            digest.update(path.relative_to(folder).as_posix().encode() + b"\0" + _sha256(path).encode() + b"\n")
        except OSError:
            digest.update(b"unreadable\n")
    return digest.hexdigest()


def trust_changes(pairing: dict[str, Any], root: Path) -> list[str]:
    expected = pairing.get("trust")
    if not isinstance(expected, dict) or not expected:
        return ["(no trust manifest; run: python3 -m babyai.usbwatch trust)"]
    current = manifest(root)
    return sorted(name for name in set(expected) | set(current) if expected.get(name) != current.get(name))


def notify(message: str) -> None:
    tool = shutil.which("notify-send")
    if not tool:
        return
    try:
        subprocess.run([tool, "BabyAI USB", message], timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _version(python: str) -> tuple[int, int] | None:
    try:
        done = subprocess.run(
            [python, "-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    try:
        major, minor = done.stdout.strip().split(".", 1)
        return int(major), int(minor)
    except ValueError:
        return None


def _has_tkinter(python: str) -> bool:
    try:
        done = subprocess.run([python, "-c", "import tkinter"], capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def choose_python(root: Path, log: Callable[[str], None] = lambda message: None) -> str:
    system = shutil.which("python3") or sys.executable
    runtime = root / "runtime" / "bin" / "python3"
    if runtime.is_file():
        runtime_version = _version(str(runtime))
        if runtime_version and runtime_version >= (3, 11) and _has_tkinter(str(runtime)):
            if not (_version(system) and _version(system) >= (3, 11) and _has_tkinter(system)):
                log(f"Using the Python bundled on the drive: {runtime}")
                return str(runtime)
    system_version = _version(system)
    if system_version is None or system_version < (3, 11):
        raise LaunchError("This Python is older than 3.11. Install Python 3.11+ or put one in <drive>/runtime/bin/python3.")
    if not _has_tkinter(system):
        raise LaunchError("This Python has no tkinter, so the desktop window can't open. Install python3-tkinter, or put a Python with tkinter in <drive>/runtime/bin/python3.")
    return system


def _manager_environment(timeout: float = 5.0) -> dict[str, str]:
    """DISPLAY / WAYLAND_DISPLAY / XAUTHORITY as the user's systemd manager has them right now.

    That is the environment KDE keeps up to date and hands to everything it starts later, so unlike this process's own
    environment (fixed at the moment the watcher started) it is current after a re-login or an Xwayland restart.  Only
    the three names are kept, and only plain values (systemd shows anything odd as ``$'...'``); nothing is read from the
    X authority file itself.  Empty if there is no systemd user manager.
    """
    tool = shutil.which("systemctl")
    if not tool:
        return {}
    try:
        done = subprocess.run([tool, "--user", "show-environment"], capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if done.returncode != 0:
        return {}
    found: dict[str, str] = {}
    for line in done.stdout.splitlines():
        name, sep, value = line.partition("=")
        if sep and name in DISPLAY_KEYS and value and not value.startswith(("$'", "'", '"')):
            found[name] = value
    return found


def _display_usable(name: str, value: str, runtime: Path, sockets: Path) -> bool:
    if name == "XAUTHORITY":
        return Path(value).is_file()
    if name == "WAYLAND_DISPLAY":
        return (Path(value) if value.startswith("/") else runtime / value).exists()
    local = re.fullmatch(r"(?:unix)?:(\d+)(?:\.\d+)?", value)
    return True if local is None else (sockets / f"X{local.group(1)}").exists()  # (a remote display can't be checked from here)


def session_display_env(
    environ: dict[str, str] | None = None, manager: Callable[[], dict[str, str]] | None = None, sockets: Path = X11_SOCKETS,
) -> dict[str, str | None]:
    """The DISPLAY / WAYLAND_DISPLAY / XAUTHORITY to give him *now*, for ``spawn``'s ``env`` (``None`` means: take it out).

    The watcher is started at login, maybe before the desktop has finished setting these up, and it can then run for days
    across Xwayland restarts, each of which gets a new random X authority file.  So its own environment is only the fallback:
    the session's current values win when they point at something that exists, then the inherited ones, and a
    XAUTHORITY naming a file that is gone is removed rather than passed on (it can only cause "Authorization required").
    Nothing is hard-coded and no cookie is read.
    """
    environ = dict(os.environ) if environ is None else environ
    live = (_manager_environment if manager is None else manager)()
    runtime = Path(environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    chosen: dict[str, str | None] = {}
    for name in DISPLAY_KEYS:
        candidates = [value for value in (live.get(name), environ.get(name)) if value]
        usable = [value for value in candidates if _display_usable(name, value, runtime, sockets)]
        if usable:
            chosen[name] = usable[0]
        elif name == "XAUTHORITY":
            chosen[name] = None
        else:
            chosen[name] = candidates[0] if candidates else None  # (my check may be wrong for an unusual setup: don't throw the value away)
    return chosen


def wait_for_display(
    log: Callable[[str], None] = lambda message: None, cancelled: Callable[[], bool] = lambda: False, timeout: float = DISPLAY_WAIT_SECONDS,
    resolve: Callable[[], dict[str, str | None]] = session_display_env, sleep: Callable[[float], None] = lambda seconds: time.sleep(seconds),
    clock: Callable[[], float] = lambda: time.monotonic(),
) -> dict[str, str | None]:
    """The display settings for a launch, waiting (a little) for the desktop to exist: after login it may not yet."""
    deadline = clock() + timeout
    told = False
    while True:
        found = resolve()
        if found.get("DISPLAY"):
            log(f"Display for him: {display_report(found)}")
            return found
        if clock() >= deadline or cancelled():
            break
        if not told:
            told = True
            log(f"The desktop isn't ready yet (no DISPLAY). Waiting up to {int(timeout)} seconds for it.")
        sleep(1.0)
    if found.get("WAYLAND_DISPLAY"):
        log("There is a Wayland display but no X display (DISPLAY), which the desktop window needs: starting him anyway.")
        return found
    raise LaunchError("There is no display (DISPLAY/WAYLAND_DISPLAY). Is this running inside your desktop login? Check: python3 -m babyai.usbwatch status")


def display_report(settings: dict[str, str | None], sockets: Path = X11_SOCKETS) -> str:
    """One line for ``status``.  The X authority file is only reported as present or not: its name and contents stay out of output and logs."""
    auth = settings.get("XAUTHORITY")
    return (
        f"DISPLAY={settings.get('DISPLAY') or '(not set)'}, WAYLAND_DISPLAY={settings.get('WAYLAND_DISPLAY') or '(not set)'}, "
        f"X authority file: {'found' if auth else 'none (the default ~/.Xauthority is used)'}"
    )


def check_noexec(mount: Path, log: Callable[[str], None] = lambda message: None) -> bool:
    options = ""
    found = mount_for_path(mount)
    if found is not None:
        options = found.options
    if "noexec" in options.split(","):
        log("This drive is mounted noexec: Python files can run, but bundled programs (runtime/Ollama) can't.")
        return False
    return True


def _pycache_prefix() -> str:
    """A stale or planted .pyc on the stick would run without its source being looked at, so none on the stick is ever read."""
    return str(Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / f"babyai-pycache-{os.getuid()}")


def _portable_env(root: Path) -> dict[str, str]:
    return {
        "BABYAI_HOME": str(root),
        "BABYAI_PAIRING": str(pairing_path()),
        "PYTHONPATH": str(root),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": _pycache_prefix(),  # compiled files are looked for here (empty), never in __pycache__ on the stick
        "XDG_CACHE_HOME": str(root / "data" / "cache"),
    }


def _app_config(root: Path) -> Path:
    return root / "config.toml"


def _server_reachable(base_url: str) -> bool:
    from urllib.parse import urlparse

    parsed = urlparse(base_url or "")
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 11434)
    import socket

    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def start_local_ollama(root: Path, config: Path, directory: Path, log: Callable[[str], None]) -> subprocess.Popen | None:
    binary = root / "ollama" / "bin" / "ollama"
    if not binary.is_file():
        return None
    settings = _config_data(config)
    base_url = str(settings.get("llm", {}).get("base_url", "http://localhost:11434/v1"))
    if _server_reachable(base_url):
        return None
    if not check_noexec(root, log):
        log("Not starting the bundled Ollama because this drive is noexec.")
        return None
    models = root / "ollama" / "models"
    models.mkdir(parents=True, exist_ok=True)
    log(f"Starting the Ollama on the drive ({binary}).")
    try:
        return spawn([str(binary), "serve"], root, directory, {"OLLAMA_MODELS": str(models)})
    except OSError as exc:
        log(f"The Ollama on the drive did not start: {exc}")
        return None


class Launched:
    """The app process, plus an optional bundled Ollama that this watcher started."""

    def __init__(self, app: subprocess.Popen, ollama: subprocess.Popen | None = None) -> None:
        self.app = app
        self.ollama = ollama

    @property
    def pid(self) -> int:
        return self.app.pid

    def poll(self) -> int | None:
        code = self.app.poll()
        if code is not None and self.ollama is not None:
            stop_process(self.ollama)
            self.ollama = None
        return code

    def stop(self) -> None:
        stop_process(self.app)
        if self.ollama is not None:
            stop_process(self.ollama)
            self.ollama = None


def _child_state_path() -> Path:
    return state_dir() / "child.json"


def _write_child_state(app_pid: int, root: Path, device: Path, ollama_pid: int | None) -> None:
    data = {"pid": app_pid, "root": str(root), "device": str(device), "ollama_pid": ollama_pid, "started": time.time()}
    _write_text_atomic(_child_state_path(), json.dumps(data))


def _read_child_state() -> dict[str, Any]:
    try:
        return json.loads(_child_state_path().read_text())
    except (OSError, ValueError):
        return {}


def _clear_child_state() -> None:
    try:
        _child_state_path().unlink()
    except OSError:
        pass


def launch_portable(pairing: dict[str, Any], directory: Path, log: Callable[[str], None], cancelled: Callable[[], bool] = lambda: False) -> Launched:
    spec = str(pairing.get("drive", ""))
    device = device_for(spec)
    if device is None:
        raise LaunchError(f"{spec or 'the drive'} is not present.")
    mount = wait_for_mount(device, 20.0, log)
    if mount is None:
        raise LaunchError("The drive is in, but it isn't mounted yet. Open it once in your file manager, then plug it in again.")
    app_folder = str(pairing.get("app_folder") or "BabyAI")
    root = mount / app_folder
    config = _app_config(root)
    if not (root / "babyai" / "__init__.py").is_file():
        raise LaunchError(f"{root} doesn't look like a BabyAI folder (no babyai/__init__.py).")
    if not config.is_file():
        raise LaunchError(f"{config} is missing. Run make-drive again or restore the config.")
    changed = trust_changes(pairing, root)
    if changed:
        shown = ", ".join(changed[:5]) + (f" and {len(changed) - 5} more" if len(changed) > 5 else "")
        message = f"The files on the drive changed ({shown}). Run: python3 -m babyai.usbwatch trust"
        notify(message)
        raise LaunchError(message)
    python = choose_python(root, log)
    display = wait_for_display(log, cancelled)  # (right after login the drive can be in before the desktop is; the checks above don't need it)
    if device_for(spec) is None:
        raise LaunchError("The drive was taken out while I waited for the desktop.")
    check_noexec(mount, log)
    ollama = start_local_ollama(root, config, directory, log)
    try:
        app = spawn([python, "-m", "babyai", "--desktop", "--config", str(config)], root, directory, {**display, **_portable_env(root)})
    except OSError:
        if ollama is not None:
            stop_process(ollama)
        raise
    _write_child_state(app.pid, root, device, ollama.pid if ollama is not None else None)
    return Launched(app, ollama)


def stop_launched(launched: Launched) -> None:
    launched.stop()
    _clear_child_state()


# ---------------------------------------------------------------------------------------------- commands


def read_spec(config: Path) -> str:
    return str(_config_data(config).get("usb", {}).get("drive", "") or "").strip()


def _config_path(args: argparse.Namespace) -> Path:
    return Path(os.path.expanduser(getattr(args, "config", "config.toml") or "config.toml")).resolve()


def _pairing_path_from(args: argparse.Namespace) -> Path:
    from_args = getattr(args, "pairing", None)
    return Path(from_args).expanduser().resolve() if from_args else pairing_path()


def _load_context(args: argparse.Namespace) -> tuple[dict[str, Any], Path, bool]:
    """(pairing, config, portable).  An old config-only setup still works."""
    pairing_file = _pairing_path_from(args)
    pairing = load_pairing(pairing_file)
    config = Path(str(pairing.get("config") or _config_path(args))).expanduser().resolve()
    portable = str(pairing.get("mode", "")).lower() == "portable"
    if not pairing:
        spec = read_spec(config)
        settings = _config_data(config).get("usb", {})
        if spec:
            if str(settings.get("mode", "")).lower() == "portable" and (config.parent / "babyai" / "__init__.py").is_file():
                portable = True
                pairing = {
                    "mode": "portable", "drive": spec, "app_folder": str(settings.get("app_folder") or "BabyAI"),
                    "grace_seconds": int(settings.get("grace_seconds", 3)),
                    "launch_if_present": bool(settings.get("launch_if_present", True)),
                    "close_on_remove": bool(settings.get("close_on_remove", True)),
                }
            else:
                pairing = {"mode": "key", "drive": spec, "config": str(config), "repo": str(repo_root())}
                portable = False
    return pairing, config, portable


def _settings(pairing: dict[str, Any], config: Path, portable: bool) -> dict[str, Any]:
    if portable:
        return pairing
    from_config = _config_data(config).get("usb", {})
    return {**from_config, **{key: value for key, value in pairing.items() if key in ("grace_seconds", "launch_if_present", "close_on_remove")}}


def _paired_spec(pairing: dict[str, Any], config: Path) -> str | None:
    spec = str(pairing.get("drive") or read_spec(config) or "").strip()
    if not spec:
        print(f"No drive is paired yet. Run: {sys.executable} -m babyai.usbwatch pair")
        return None
    try:
        parse_spec(spec)
    except ValueError as exc:
        print(exc)
        return None
    return spec


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


def _current_trust(pairing: dict[str, Any], pairing_file: Path) -> dict[str, Any]:
    """``pairing`` with the trust manifest as it is on disk *now*.

    The watcher runs for days, and ``trust`` rewrites the pairing file while it does: a launch that used the copy read at start-up would
    keep refusing files the person has just trusted.  Only the manifest is taken, and only if the file is still for the same drive;
    an unreadable or different file leaves what the watcher already has.
    """
    fresh = load_pairing(pairing_file)
    manifest_now = fresh.get("trust")
    if fresh.get("drive") != pairing.get("drive") or not isinstance(manifest_now, dict) or not manifest_now:
        return pairing
    return {**pairing, "trust": manifest_now}


def cmd_run(args: argparse.Namespace) -> int:
    pairing, config, portable = _load_context(args)
    spec = _paired_spec(pairing, config)
    if spec is None:
        return 2
    settings = _settings(pairing, config, portable)
    directory = state_dir()
    lock = take_lock(directory)
    if lock is None:
        print("The USB watcher is already running.")
        return 0
    log = _log_to_file(directory)
    done = threading.Event()
    log(f"Watching for {spec} ({'portable' if portable else 'key'} mode, config {config}).")
    if portable:
        present = lambda: is_present(spec)  # noqa: E731
        pairing_file = _pairing_path_from(args)
        launch = lambda: launch_portable(_current_trust(pairing, pairing_file), directory, log, done.is_set)  # noqa: E731
        stop = stop_launched
    else:
        present = lambda: is_present(spec)  # noqa: E731
        launch = lambda: spawn(launch_command(config), repo_root(), directory, wait_for_display(log, done.is_set))  # noqa: E731
        stop = stop_process
    watcher = Watcher(
        present, launch, stop,
        elsewhere=another_babyai_running, grace=float(settings.get("grace_seconds", 3)),
        launch_if_present=bool(settings.get("launch_if_present", True)), close_on_remove=bool(settings.get("close_on_remove", True)), log=log,
    )
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: done.set())
    try:
        watch(watcher, done, POLL_SECONDS, log)
    finally:
        lock.close()
    log("Stopped watching. (If he is running he stays running.)")
    return 0


def _context_for_install(args: argparse.Namespace) -> tuple[dict[str, Any], Path, bool]:
    pairing, config, portable = _load_context(args)
    if not pairing:
        return {}, config, portable
    if not portable:
        config = Path(str(pairing.get("config") or config)).expanduser().resolve()
    return pairing, config, portable


def cmd_install(args: argparse.Namespace) -> int:
    pairing, config, portable = _context_for_install(args)
    if _paired_spec(pairing, config) is None:
        return 2
    pairing_file = _pairing_path_from(args)
    if not pairing_file.exists():
        save_pairing(pairing, pairing_file)
    if not portable:
        config = Path(str(pairing.get("config") or config)).expanduser().resolve()
    try:
        path = install_login_item(None if portable else config, pairing_file)
    except (OSError, ValueError) as exc:
        print(f"Could not install the login item: {exc}")
        return 2
    stop_watcher(state_dir())  # (a watcher already running has the old settings)
    custom = pairing_file if pairing_file.resolve() != pairing_path().resolve() else None
    if custom is None:
        start_watcher(None if portable else config)
    else:
        start_watcher(None if portable else config, custom)
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
    pairing, config, portable = _load_context(args)
    spec = str(pairing.get("drive") or read_spec(config) or "").strip()
    print(f"Pairing: {_pairing_path_from(args)}")
    print(f"Mode: {'portable' if portable else 'key'}")
    print(f"Config: {config}")
    print(f"Paired drive: {spec or '(none: run pair)'}")
    if spec:
        try:
            print(f"Drive plugged in now: {'yes' if is_present(spec) else 'no'}")
        except ValueError as exc:
            print(exc)
    if portable:
        device = device_for(spec) if spec else None
        mount = mount_point(device) if device is not None else None
        print(f"App folder: {mount / str(pairing.get('app_folder') or 'BabyAI') if mount else '(drive not mounted)'}")
    print(f"Login item: {'installed' if autostart_path().exists() else 'not installed'} ({autostart_path()})")
    print(f"Display he would get now: {display_report(session_display_env())}")
    print(f"Installed watcher: {stub_path() if stub_path().exists() else 'not installed'}")
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


def _uuid_for(device: str | Path) -> str | None:
    found = shutil.which("findmnt")
    if found:
        try:
            done = subprocess.run([found, "-no", "UUID", "--target", str(device)], capture_output=True, text=True, timeout=10)
            if done.returncode == 0 and done.stdout.strip():
                return done.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        for link in sorted((DISK / "by-uuid").iterdir()):
            if os.path.realpath(link) == os.path.realpath(str(device)):
                return link.name
    except OSError:
        pass
    found = shutil.which("lsblk")
    if found:
        try:
            done = subprocess.run([found, "-no", "UUID", str(device)], capture_output=True, text=True, timeout=10)
            if done.returncode == 0 and done.stdout.strip():
                return done.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def _source_for(mount: Path) -> str:
    found = mount_for_path(mount)
    if found is not None:
        return found.source
    return ""


def cmd_pair(args: argparse.Namespace) -> int:
    config = _config_path(args)
    pairing_file = _pairing_path_from(args)
    if args.spec:
        try:
            parse_spec(args.spec)
        except ValueError as exc:
            print(exc)
            return 2
        spec = args.spec.strip()
        device = device_for(spec)
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
        device = Path(drive.device)
    if args.portable:
        mount = Path(args.mount).expanduser().resolve() if args.mount else (mount_point(device) if device else None)
        if mount is None:
            print("The drive isn't mounted yet. Open it once in your file manager, or pass --mount.")
            return 1
        app_folder = args.app_folder or str(_config_data(config).get("usb", {}).get("app_folder") or "BabyAI")
        root = mount / app_folder
        if not (root / "babyai" / "__init__.py").is_file():
            print(f"{root} doesn't look like a BabyAI folder. Run make-drive first.")
            return 1
        if not spec.startswith("uuid:"):
            print("A portable drive is recognised by its filesystem UUID. Use --by uuid, or run make-drive.")
            return 1
        settings = _config_data(root / "config.toml").get("usb", {})
        pairing = {
            "mode": "portable", "drive": spec, "app_folder": app_folder,
            "grace_seconds": int(settings.get("grace_seconds", 3)),
            "launch_if_present": bool(settings.get("launch_if_present", True)),
            "close_on_remove": bool(settings.get("close_on_remove", True)),
            "trust": manifest(root),
        }
        save_pairing(pairing, pairing_file)
        print(f"Paired the portable drive {spec} ({root}).")
    else:
        write_setting(config, "usb", "mode", "key")
        write_setting(config, "usb", "drive", spec)
        pairing = {
            "mode": "key", "drive": spec, "config": str(config), "repo": str(repo_root()),
            "grace_seconds": int(_config_data(config).get("usb", {}).get("grace_seconds", 3)),
            "launch_if_present": bool(_config_data(config).get("usb", {}).get("launch_if_present", True)),
            "close_on_remove": bool(_config_data(config).get("usb", {}).get("close_on_remove", True)),
        }
        save_pairing(pairing, pairing_file)
        print(f"Saved to {config}: [usb] drive = {spec!r}")
    if args.no_install:
        print("Not installing the login item (--no-install). Run `python3 -m babyai.usbwatch run` to try it in this window.")
        return 0
    return cmd_install(args)


def _can_ask() -> bool:
    """Trusting changed files is the user's call alone: it is only asked at a real keyboard."""
    return sys.stdin.isatty()


def cmd_trust(args: argparse.Namespace) -> int:
    pairing, _config, portable = _load_context(args)
    if not portable:
        print("This pairing uses the drive only as a key, so there is no file manifest to trust.")
        return 1
    spec = str(pairing.get("drive", ""))
    device = device_for(spec)
    if device is None:
        print("The drive is not plugged in.")
        return 1
    mount = mount_point(device)
    if mount is None:
        print("The drive is plugged in but not mounted. Open it once, then run trust again.")
        return 1
    root = mount / str(pairing.get("app_folder") or "BabyAI")
    if not (root / "babyai" / "__init__.py").is_file():
        print(f"{root} doesn't look like a BabyAI folder.")
        return 1
    changed = trust_changes(pairing, root)
    if not changed or changed == ["(no trust manifest; run: python3 -m babyai.usbwatch trust)"]:
        if changed:
            print("There is no trust manifest yet. Everything in the app folder is treated as new.")
        else:
            print("Everything in the app folder matches the trusted copy.")
            return 0
    print("These files are new, changed or missing since the last trust:")
    for name in changed:
        print(f"  {name}")
    # Trust is deliberately interactive: no flag, and no script or model-issued command, may approve changed code.
    if not _can_ask():
        print("Run this yourself in a terminal: the yes has to come from your keyboard, never from a script or from him.")
        return 1
    try:
        answer = input("Trust them and update the manifest? [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        print("Not trusted. He will refuse to start from this drive until you run trust again.")
        return 1
    data = dict(pairing)
    data["trust"] = manifest(root)
    save_pairing(data, _pairing_path_from(args))
    print(f"Trusted {len(data['trust'])} entries (Python files, git settings and bundled programs). You can plug the drive in now.")
    return 0


def _findmnt_line(mount: Path) -> str:
    tool = shutil.which("findmnt")
    if not tool:
        found = mount_for_path(mount)
        return f"{mount} {found.source} {found.fstype} {found.options}" if found else f"{mount} (findmnt not installed)"
    try:
        done = subprocess.run([tool, "-no", "TARGET,SOURCE,FSTYPE,OPTIONS,UUID", "--target", str(mount)], capture_output=True, text=True, timeout=10)
        return done.stdout.strip() or f"{mount} (findmnt found no mount here)"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{mount} (findmnt failed: {exc})"


def _copy_tree(source: Path, destination: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache", ".ruff_cache", "*.tmp")
    shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=False, ignore=ignore, copy_function=shutil.copyfile)


def _copy_memory_database(source: Path, destination: Path) -> bool:
    if not source.is_file():
        return False
    temp = destination.with_name(destination.name + ".tmp")
    try:
        source_db = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=10)
        target_db = sqlite3.connect(str(temp), timeout=10)
        try:
            source_db.backup(target_db)
            target_db.execute("PRAGMA journal_mode = DELETE")
            target_db.execute("PRAGMA synchronous = FULL")
        finally:
            target_db.close()
            source_db.close()
        os.replace(temp, destination)
        return True
    finally:
        try:
            temp.unlink()
        except OSError:
            pass


def _copy_data(source: Path, destination: Path, overwrite: bool, log: Callable[[str], None]) -> None:
    ignored_patterns = shutil.ignore_patterns(
        "__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache",
        ".ruff_cache", "*.tmp",
    )
    self_dir = source / "self"

    def ignore_data(directory: str, names: list[str]) -> set[str]:
        ignored = set(ignored_patterns(directory, names))
        if Path(directory) == self_dir:
            ignored.add("worktrees")
        return ignored

    def copy_data_file(src: str, dst: str) -> str:
        target = Path(dst)
        if overwrite or not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        return dst

    for name in ("habits", "personality", "reminders", "self"):
        folder = source / name
        if folder.is_dir():
            shutil.copytree(
                folder,
                destination / name,
                dirs_exist_ok=True,
                symlinks=False,
                copy_function=copy_data_file,
                ignore=ignore_data,
            )

    memory = source / "memory.db"
    if memory.is_file() and (overwrite or not (destination / "memory.db").exists()):
        if _copy_memory_database(memory, destination / "memory.db"):
            log(f"Copied memory from {memory}.")
        else:
            log(f"Could not copy {memory}; starting with an empty memory.")


FAT_LIKE = frozenset({"vfat", "msdos", "exfat", "ntfs", "ntfs3", "fuseblk"})  # (no real file modes: every file shows as executable)


def _git_ignore_file_modes(root: Path, log: Callable[[str], None]) -> None:
    """Without this git sees every file on a FAT stick as edited, and self-upgrade refuses to run on a "dirty" folder."""
    tool = shutil.which("git")
    config = root / ".git" / "config"
    if not tool or not config.is_file():
        log("git isn't installed here, so I could not set core.fileMode=false on the stick's copy; do it once there before an upgrade.")
        return
    try:
        done = subprocess.run([tool, "config", "--file", str(config), "core.fileMode", "false"], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"Could not set core.fileMode=false on the stick's copy: {exc}")
        return
    if done.returncode != 0:
        log(f"Could not set core.fileMode=false on the stick's copy: {done.stderr.strip()}")


def _portable_config_text(source_config: Path | None, spec: str, app_folder: str) -> str:
    text = source_config.read_text() if source_config and source_config.is_file() else ""
    text = set_toml_value(text, "usb", "drive", spec)
    text = set_toml_value(text, "usb", "mode", "portable")
    text = set_toml_value(text, "usb", "app_folder", app_folder)
    return text


def cmd_make_drive(args: argparse.Namespace) -> int:
    mount = Path(args.mount).expanduser().resolve()
    if not mount.is_dir():
        print(f"{mount} is not a folder. Plug the drive in and give me the folder it is mounted at.")
        return 1
    found = mount_for_path(mount)
    if found is None:
        print(f"{mount} is not a mounted filesystem. Run findmnt and give me the mount point, not a folder inside it.")
        return 1
    app_folder = args.app_folder or "BabyAI"
    root = mount / app_folder
    if root.exists() and any(root.iterdir()) and not args.force:
        print(f"{root} already has files. I won't overwrite them without --force.")
        return 1
    source = Path(args.source).expanduser().resolve() if args.source else (repo_root() if (repo_root() / "babyai" / "__init__.py").is_file() else Path.cwd().resolve())
    if not (source / "babyai" / "__init__.py").is_file():
        print(f"{source} doesn't look like the BabyAI repository (no babyai/__init__.py). Pass --source.")
        return 1
    if source == root or source in root.parents:
        print("Refusing to copy the app into itself. Choose a mount outside the repository.")
        return 1
    device = _source_for(mount)
    uuid = (str(args.uuid).removeprefix("uuid:") if args.uuid else None) or _uuid_for(mount) or (_uuid_for(device) if device else None)
    spec = f"uuid:{uuid}" if uuid else ""
    if not spec or not spec.startswith("uuid:") or spec == "uuid:":
        print("I can't read the drive's filesystem UUID. Run findmnt, then pass --uuid <the UUID> (without 'uuid:').")
        return 1
    print(f"Drive: {mount}")
    print(_findmnt_line(mount))
    print(f"I will write the app to: {root}")
    if root.exists() and any(root.iterdir()):
        print("That folder is not empty; --force was given, so existing app files will be replaced. data/ is kept unless --overwrite-data.")
    if args.yes:
        answer = "yes"
    else:
        try:
            answer = input("Write BabyAI to this drive now? Type yes to continue: ")
        except EOFError:
            answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        print("Nothing written.")
        return 1
    root.mkdir(parents=True, exist_ok=True)
    log: Callable[[str], None] = print
    for name in ("babyai", "tests", "capri", "assets", "docs"):
        folder = source / name
        if folder.is_dir():
            log(f"Copying {name}/ ...")
            _copy_tree(folder, root / name)
    for name in ("pyproject.toml", "README.md", "LICENSE", "NOTICE"):
        file = source / name
        if file.is_file():
            shutil.copyfile(file, root / name)
    if not args.no_git and (source / ".git").is_dir():
        log("Copying .git/ (self-upgrade needs it; use --no-git to skip) ...")
        _copy_tree(source / ".git", root / ".git")
        if found.fstype in FAT_LIKE:
            _git_ignore_file_modes(root, log)
    config = root / "config.toml"
    source_config = source / "config.toml"
    _write_text_atomic(config, _portable_config_text(source_config, spec, app_folder))
    data = root / "data"
    for name in ("", "cache", "habits", "personality", "reminders", "self"):
        (data / name).mkdir(parents=True, exist_ok=True)
    source_data = Path(args.data_from).expanduser().resolve() if args.data_from else Path("~/.local/share/babyai").expanduser().resolve()
    if not args.no_data:
        _copy_data(source_data, data, args.overwrite_data, log)
    settings = _config_data(config).get("usb", {})
    pairing = {
        "mode": "portable", "drive": spec, "app_folder": app_folder,
        "grace_seconds": int(settings.get("grace_seconds", 3)),
        "launch_if_present": bool(settings.get("launch_if_present", True)),
        "close_on_remove": bool(settings.get("close_on_remove", True)),
        "trust": manifest(root),
    }
    pairing_file = _pairing_path_from(args)
    save_pairing(pairing, pairing_file)
    print(f"Paired {spec}; recorded {len(pairing['trust'])} Python files in {pairing_file}.")
    if args.no_install:
        print("Not installing the login item (--no-install). Try: python3 -m babyai.usbwatch run")
        return 0
    try:
        path = install_login_item(None, pairing_file)
    except (OSError, ValueError) as exc:
        print(f"Could not install the login item: {exc}")
        return 2
    stop_watcher(state_dir())
    custom = pairing_file if pairing_file.resolve() != pairing_path().resolve() else None
    start_watcher(None, custom)
    print(f"Installed: {path}")
    print("Log out and back in (or leave the watcher running) and plug the drive in. Use the right-click Eject USB menu before removing it.")
    return 0


def _cmdline(pid: int) -> list[bytes]:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return []


def _is_the_app(pid: int, root: Path | None) -> bool:
    argv = _cmdline(pid)
    looks_like_babyai = any(part == b"babyai" or part.endswith(b"babyai/__main__.py") for part in argv)
    if not looks_like_babyai or b"--desktop" not in argv:
        return False
    if root is None:  # (the mount is gone; the desktop flag and package are the best check available)
        return True
    return str(root / "config.toml").encode() in argv


def _is_the_ollama(pid: int, root: Path | None) -> bool:
    argv = _cmdline(pid)
    if not any(part.endswith(b"ollama") and b"bin" in part for part in argv):
        return False
    if b"serve" not in argv:
        return False
    return root is None or any(str(root).encode() in part for part in argv)


def cmd_eject(args: argparse.Namespace) -> int:
    pairing, _config, portable = _load_context(args)
    if not portable:
        print("Eject USB is for a portable drive. Use your desktop's Safely Remove for a key-only drive.")
        return 1
    spec = str(pairing.get("drive", ""))
    device = device_for(spec)
    if device is None:
        print("The drive is not plugged in.")
        return 1
    mount = mount_point(device)
    root = mount / str(pairing.get("app_folder") or "BabyAI") if mount else None
    state = _read_child_state()
    pid = args.pid or state.get("pid")
    if pid:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            pid = None
        if pid is not None and _is_the_app(pid, root):
            print(f"Closing BabyAI (pid {pid}) ...")
            stop_pid(pid)
        else:
            print("The app wasn't running from this drive, so I left every process alone.")
    ollama_pid = state.get("ollama_pid")
    if ollama_pid and ollama_pid != pid:
        try:
            ollama_pid = int(ollama_pid)
        except (TypeError, ValueError):
            ollama_pid = None
        if ollama_pid is not None and _is_the_ollama(ollama_pid, root):
            stop_pid(ollama_pid)
    # Keep the host watcher alive: it must continue watching after an eject so reinserting the stick starts him again.
    _clear_child_state()
    udisksctl = shutil.which("udisksctl")
    if not udisksctl:
        print("udisksctl isn't installed, so I can't unmount the drive for you. Close any open files and use your desktop's Safely Remove.")
        return 1
    try:
        os.chdir("/")  # (this command may have been started from the drive; its cwd would keep the mount busy)
    except OSError:
        pass
    deadline = time.monotonic() + 20.0
    while True:
        try:
            done = subprocess.run([udisksctl, "unmount", "-b", str(device)], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"Could not unmount: {exc}")
            return 1
        if done.returncode == 0:
            print(done.stdout.strip() or "Unmounted. You can remove the drive.")
            return 0
        if time.monotonic() >= deadline:
            print((done.stderr or done.stdout).strip() or "The drive is still busy. Close any open files and try again.")
            return 1
        time.sleep(1.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m babyai.usbwatch", description="Start him when your USB drive is plugged in and close him when it is taken out.")
    common = argparse.ArgumentParser(add_help=False)  # (after the command: `... pair --config ~/babyai/config.toml`)
    common.add_argument("--config", default="config.toml", help="Path to config.toml (default: the one in the folder you run this from, like babyai itself)")
    common.add_argument("--pairing", help="Path to the pairing record (default: ~/.config/babyai-usb/pairing.toml)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", parents=[common], help="show the USB drives plugged in now").set_defaults(run=cmd_list)
    pair = sub.add_parser("pair", parents=[common], help="choose the drive, remember it, and install the login item")
    pair.add_argument("--spec", help="skip the list: uuid:..., label:... or id:... exactly as `list` prints it")
    pair.add_argument("--pick", type=int, help="the number from `list`")
    pair.add_argument("--by", choices=sorted(KINDS), help="recognise the drive by its filesystem id, its label, or the hardware itself")
    pair.add_argument("--no-install", action="store_true", help="only remember the drive")
    pair.add_argument("--portable", action="store_true", help="the app itself lives on the drive (make-drive does this for you)")
    pair.add_argument("--mount", help="the mount point of the drive, for a portable pair")
    pair.add_argument("--app-folder", default="BabyAI", help="the folder on the drive that holds the app (default: BabyAI)")
    pair.set_defaults(run=cmd_pair)
    make = sub.add_parser("make-drive", parents=[common], help="copy BabyAI, its assets, config and data to a mounted drive and pair it")
    make.add_argument("mount", help="the mounted folder of the drive, e.g. /run/media/you/BENNY")
    make.add_argument("--force", action="store_true", help="write into a non-empty BabyAI folder")
    make.add_argument("--overwrite-data", action="store_true", help="replace existing copied data files on the drive")
    make.add_argument("--no-data", action="store_true", help="do not copy memory, habits, personality or self-upgrade notes")
    make.add_argument("--data-from", help="where to copy data from (default: ~/.local/share/babyai)")
    make.add_argument("--source", help="the checkout to copy (default: this one)")
    make.add_argument("--app-folder", default="BabyAI", help="folder to create on the drive (default: BabyAI)")
    make.add_argument("--uuid", help="the drive's filesystem UUID, if findmnt cannot read it")
    make.add_argument("--no-git", action="store_true", help="do not copy .git/ (saves space; self-upgrade is unavailable)")
    make.add_argument("--no-install", action="store_true", help="copy and pair, but do not install the login item")
    make.add_argument("--yes", action="store_true", help="skip the findmnt confirmation (for tests; use it only when you are sure)")
    make.set_defaults(run=cmd_make_drive)
    sub.add_parser("run", parents=[common], help="watch in the foreground (what the login item runs)").set_defaults(run=cmd_run)
    sub.add_parser("install", parents=[common], help="install the login item and start the watcher now").set_defaults(run=cmd_install)
    sub.add_parser("uninstall", parents=[common], help="remove the login item and stop the watcher").set_defaults(run=cmd_uninstall)
    sub.add_parser("status", parents=[common], help="what is set up, and what the watcher has been doing").set_defaults(run=cmd_status)
    trust = sub.add_parser("trust", parents=[common], help="list and accept changed Python files on a portable drive")
    trust.set_defaults(run=cmd_trust)
    eject = sub.add_parser("eject", parents=[common], help="close this app and unmount the portable drive")
    eject.add_argument("--pid", type=int, help="the app pid requesting the eject (checked before it is stopped)")
    eject.set_defaults(run=cmd_eject)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
