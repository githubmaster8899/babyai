# Running BabyAI from a USB drive

This is the Linux USB support: a small host-side launcher watches for one
paired stick, starts BabyAI when it appears, and closes it when it is taken out.
The app itself still runs as the normal Python program. There is no framework
switch and no new dependency, and the old key-only setup is still the default.

## Two modes

| | key (default) | portable |
|---|---|---|
| Where the app is | this checkout | `<mount>/BabyAI/` on the stick |
| What is on the stick | nothing BabyAI reads | app, Capri assets, config, `data/` |
| How it is recognised | `uuid:...`, `label:...` or hardware `id` | filesystem `uuid:...` only |
| Data location | your normal `~/.local/share/babyai` | `<mount>/BabyAI/data/` |
| Changes on the stick checked? | no | SHA-256 manifest of every `.py`, the git settings and any bundled programs |

Key mode is deliberately unchanged: plugging the stick in is just a trigger.
Portable mode runs the copy on the stick, so the same stick can be carried to
another machine with the same Linux user name and a suitable Python.

## Requirements

* Linux with `/proc/self/mountinfo` and `/dev/disk` (udev).
* Python 3.11 or newer, with Tkinter for the desktop overlay.
* Optional: a Python with Tkinter at `<drive>/runtime/bin/python3`.
* Optional: an Ollama binary at `<drive>/ollama/bin/ollama`. It is used only if
  it is already there; nothing is ever downloaded.
* `findmnt` for `make-drive` and `udisksctl` for **Eject USB**. Without
  `udisksctl`, use the desktop's **Safely Remove** instead.

The launcher is a single file installed at
`~/.local/share/babyai-usb/usbwatch.py` (or under `XDG_DATA_HOME`). It imports
nothing from the `babyai` package, so it can run when the drive is gone. The
pairing record is `~/.config/babyai-usb/pairing.toml` (or under
`XDG_CONFIG_HOME`).

## Key mode: pair once

1. Plug the stick in.
2. Find the way to recognise it:

   ```sh
   python3 -m babyai.usbwatch list
   ```

   Prefer `uuid:...`: it stays the same in any USB port and changes only if the
   filesystem is reformatted. `label:` is convenient but not unique.
3. Pair it:

   ```sh
   python3 -m babyai.usbwatch pair
   ```

   `pair` asks which drive, writes `[usb] drive = "uuid:..."` into
   `config.toml`, records the pairing, installs the login item, and starts the
   watcher. Use `--no-install` to stop before the last two steps, or
   `--spec uuid:ABCD-1234` to skip the list.

The watcher starts `python3 -m babyai --desktop --config <your config>` from
this checkout. Taking the stick out closes that process after `grace_seconds`
(default 3). It never opens or changes the stick in key mode.

Useful commands:

```sh
python3 -m babyai.usbwatch status
python3 -m babyai.usbwatch run          # foreground, for debugging
python3 -m babyai.usbwatch uninstall    # remove the login item and stop watching
```

## Portable mode: create the stick

The stick must already be formatted and mounted. **`make-drive` does not
partition, format, or erase anything.** It writes only under
`<mount>/<app-folder>` (and installs the host launcher).

```sh
findmnt -no TARGET,SOURCE,FSTYPE,UUID --target "/run/media/you/BENNY"
python3 -m babyai.usbwatch make-drive "/run/media/you/BENNY"
```

`make-drive`:

* shows the `findmnt` line and asks for `yes` before the first write;
* refuses a non-empty `<mount>/BabyAI/` unless `--force`;
* copies `babyai/`, `tests/`, `capri/`, `assets/`, `docs/`, the licence files,
  `pyproject.toml`, `README.md`, and `.git/` (skip the last with `--no-git`);
* writes a portable `config.toml` with the filesystem UUID, the app folder, and
  `[usb] mode = "portable"`;
* copies memory using SQLite's online backup, plus habits, personality,
  reminders and self-upgrade notes from `~/.local/share/babyai`;
* on FAT/exFAT/NTFS sticks, sets `core.fileMode = false` in the copied `.git/config`
  (those filesystems have no execute bit, so git would otherwise report every
  file as modified and the self-upgrade tests would refuse to run);
* records the SHA-256 manifest and installs/starts the host watcher.

Useful options: `--data-from PATH`, `--no-data`, `--overwrite-data`,
`--app-folder NAME`, `--uuid UUID`, and `--no-install`. By default, existing files
on the stick are preserved and only missing data files are copied. `--overwrite-data`
replaces matching files from the computer while keeping USB-only files. `--no-data`
skips the data copy entirely. The default app folder is `BabyAI`; the mount point
may contain spaces.

The launcher resolves the stick by filesystem UUID, waits about 20 seconds for
the desktop to automount it, and only then falls back to `udisksctl mount`. It
checks the Python version, Tkinter, the display (see below), and whether the
drive is mounted `noexec` (bundled runtime/Ollama programs cannot run from a
`noexec` mount). Python compiled files are never read from or written to the
stick: `PYTHONPYCACHEPREFIX` points at an empty folder under `XDG_RUNTIME_DIR`.

## Starting at login, and the display

`pair` and `make-drive` install `~/.config/autostart/babyai-usb.desktop`, so the
watcher starts with your KDE session. The watcher can start before the desktop
has finished setting up its display, and it can keep running across an
Xwayland restart. Each restart gets a new random X authority file, so the
`DISPLAY`/`XAUTHORITY` that the watcher inherited at login can be out of date
("Authorization required, but no authorization protocol specified", "couldn't
connect to display"). So, at every launch the watcher:

1. asks your session's systemd user manager for the current `DISPLAY`,
   `WAYLAND_DISPLAY` and `XAUTHORITY` (`systemctl --user show-environment`, the
   same values KDE hands to every program it starts), and prefers those when
   they point at something that exists;
2. falls back to the values it inherited, and removes an `XAUTHORITY` that names
   a file that is gone instead of passing it on;
3. waits up to 45 seconds if there is no `DISPLAY` yet, saying so once in the log,
   and checks that the drive is still in afterwards.

No file name is built in, and the authority file is only checked to exist: it is
never read, and neither its name nor its contents go to the log or to `status`.

To see what he would be given right now:

```sh
python3 -m babyai.usbwatch status
```

A launch is tried once per plug-in (waiting for the desktop counts as part of
that try). If it failed, the reason is in the log; unplug and replug the stick
to try again.

## Trusting changes on a portable stick

A portable pairing includes a SHA-256 manifest of everything on the stick that
runs as you: every `.py` file under the app folder, git's own `.git/config`,
`.git/info/attributes` and non-sample hooks, and, if they are there, the bundled
`runtime/` and `ollama/bin` programs (one combined digest each). `data/` and
caches are not covered, since they are his to change. Before launch, the launcher
recomputes it. If anything is new, changed or missing, it:

* logs the mismatch and sends a desktop notification;
* refuses to launch;
* tells you to run `trust`.

The `trust` command lists the changed files and requires an interactive
confirmation before updating the manifest. It has to be run in a real terminal:
with no keyboard attached (a pipe, a script, a tool call) it refuses. There is no
non-interactive bypass:

```sh
# from the app folder on the stick, or with the package on PYTHONPATH
python3 -m babyai.usbwatch trust
```

`trust` is a command-line action only. The model has no tool that can run it
(the shell tool refuses any command naming `usbwatch` or `babyai-usb`), and the
launcher files and trust tests are on the protected list. A failed
manifest check is never retried in a loop while the stick stays plugged in;
unplug and replug after trusting.

Changes the user approves through the existing self-upgrade flow
(`upgrade_code apply`, `upgrade_version update`, rollback) refresh the manifest
at the end of that operation, because the user already approved those files.
A manual edit on the stick still needs `trust`.

A stick paired before the manifest grew to cover git's settings and the bundled
programs will, the first time after the launcher is updated, list `.git/config`
(and any hooks or bundled programs) as new and refuse to launch. That is expected:
look at the list, then run `python3 -m babyai.usbwatch trust` once in a terminal
and answer `y`.

## Removing the stick safely

Use the right-click **Eject USB** menu in the overlay. It:

1. verifies the pid is the BabyAI instance for this drive before stopping it;
2. stops the bundled Ollama if the current watcher started one;
3. leaves the host watcher running so it can detect the drive's removal
   and launch BabyAI again when the drive is reinserted;
4. runs `udisksctl unmount -b <device>`, retrying for a short while if the
   desktop is still busy.

If `udisksctl` is missing or the drive stays busy, close anything else using
the stick and use the desktop's **Safely Remove**. Do not pull a portable stick
while the app is writing. The memory database uses `journal_mode=DELETE` (no
`-wal` file), `synchronous=FULL`, an integrity check at startup, and atomic
writes for the JSON state files. Disk I/O errors from a removal are swallowed,
remembered by the memory tool, and the watcher closes the app after the grace
period.

## Troubleshooting

| What you see | Look at / try |
|---|---|
| Nothing opens when the stick goes in | `python3 -m babyai.usbwatch status`: is the watcher running, the drive plugged in, a display shown? |
| "Could not start him: ..." | `tail ~/.local/state/babyai/usbwatch.log` says why. Unplug and replug to try again. |
| He starts and the window never appears | `tail ~/.local/state/babyai/babyai-from-usb.log` (his own output; display errors show up here). |
| "files on the drive changed" notification | You, or the self-upgrade flow outside its normal path, changed code on the stick. Look at the listed files, then run `python3 -m babyai.usbwatch trust` yourself. |
| "not mounted yet" | Open the drive once in the file manager; replug. `udisksctl` must exist for the fallback mount. |
| Eject says the drive is busy | Close anything else showing the stick (a file manager window, a terminal in it) and use **Eject USB** again. |
| The watcher is not running after login | `python3 -m babyai.usbwatch status`. Run `install` again from your desktop session (below). `journalctl --user -b -g usbwatch` and `~/.local/state/babyai/usbwatch-run.log` have anything it printed. |

The logs are in `~/.local/state/babyai/` (or under `XDG_STATE_HOME`): `usbwatch.log`
(what the watcher decided), `usbwatch-run.log` (what it printed), and
`babyai-from-usb.log` (his own output). The first and last are started afresh
when they pass 1 MB. The login item's own output goes to the session journal,
not to `usbwatch-run.log`, which only has what a watcher started by `install` printed.

After updating the launcher code, install it again so the login item and the
running watcher use the new copy. For portable mode that is:

```sh
python3 -m babyai.usbwatch install --config "$HOME/babyai/config.toml"
```

It stops the old watcher and starts the new one. A BabyAI that is already
running is left alone, and nothing on the stick is touched.

## Removing the launcher

```sh
python3 -m babyai.usbwatch uninstall
```

removes the login item and stops the watcher. It never touches the stick, its
data, or the pairing record. To forget the pairing too, delete
`~/.config/babyai-usb/` and `~/.local/share/babyai-usb/` yourself; the stick and
everything on it stay as they are.

## Data layout on a portable stick

```text
<mount>/BabyAI/
  babyai/          the app
  capri/           Capri assets and licence
  assets/          Capri runtime assets
  config.toml      portable config
  data/
    memory.db      SQLite memory
    habits/
    personality/
    reminders/
    self/          upgrade notes, journal and skills
    cache/         XDG_CACHE_HOME
  runtime/bin/python3   optional
  ollama/bin/ollama     optional
```

`HOME` is not changed. Only the data paths and `XDG_CACHE_HOME` point at the
stick; the launcher's own log (`~/.local/state/babyai/usbwatch.log`) and the
pairing record stay on the host.

## Real-drive check, 2026-11-14

Checked on the `BENNY_S AI` vfat stick (114.4 GB, mounted at
`/run/media/ItsBenny/BENNY_S AI`) from commit `dcc5d83`:

* `make-drive --force --no-install --uuid 2154-5D47` replaced the app files in
  an existing `BabyAI/` folder; the existing `data/` files kept their names,
  sizes and mtimes.
* The generated config contains only `[usb]` (no stale `[portable]` table).
* A 92-file SHA-256 manifest matched every `.py` file on the stick.
* Appending one line to a real `.py` file on the stick made `trust` list it
  and refuse with exit 1; answering `yes` on a copy of the pairing approved it,
  and restoring the file returned the real pairing to zero changes.
* `launch_portable()` refused the edited file before starting a process, with
  the "files on the drive changed … run trust" message.
* The on-drive `data/memory.db` reports `journal_mode=DELETE`,
  `synchronous=FULL`, `integrity_check=ok`, and has no `-wal`/`-shm` sidecars.
* A text and a bytes atomic write on the stick left no `.tmp` file and were
  removed afterwards.
* `~/.local/share/babyai` and `~/.cache/babyai` were byte-for-byte unchanged
  (listings and SHA-256) after `make-drive` and the checks.
* `capri/LICENSE.md`, `NOTICE` and `LICENSE` match the host copies; no
  `__pycache__` or `.pyc` files were copied to the stick.
* With the stick unplugged, `status` reports "Drive plugged in now: no" and
  the watcher is not running.

The shell used for this check hides `/dev/disk` and `/dev/sdb1`, so the real
`trust` command was exercised through the same functions with a temporary
fake `/dev/disk` symlink and mountinfo entry. The files it checked were the
real files on the stick.

### Not tested on this run

These still need a real desktop session and a real `/dev/disk`:

* a **fresh, empty** stick (this run used an existing `BabyAI/` with `--force`);
* starting the Tk app from the stick (Tkinter is not installed here);
* remembering a fact and setting a reminder while the app is up;
* unplug/replug with the app running, pulling the stick mid-use, and a
  post-removal database read;
* KDE's Safely Remove reporting the drive busy, the Eject USB menu, and
  `udisksctl` unmount;
* logout/login with the stick in, and a session with no stick;
* installing/starting the real login item and watcher (run was `--no-install`);
* optional `<root>/runtime/bin/python3` and `<root>/ollama/bin/ollama`;
* Python 3.11 (only 3.14.9 was available here), ruff, `xvfb-run`, and
  Tk-dependent tests;
* macOS and Windows.

### Checked by hand on the same stick

Separately from the run above, on Bazzite with KDE Plasma on Wayland: the drive
is detected; removal and reinsertion are detected; it is mounted automatically;
BabyAI starts from the stick and the Capri window opens; the stick's `data/` and
config folders are there; the login item installs; and the watcher keeps running
after its terminal is closed. These were run by the maintainer, not by the test suite.

### What the test suite covers

The fake `/dev/disk`, fake mountinfo, trust changes, copying, config rewriting,
SQLite settings, atomic writes, the display lookup, and the launch and eject
commands (with a fake `udisksctl` and `systemctl`).

FAT32 has no real Unix execute bits, fakes ownership, is case-insensitive and
has a 4 GB per-file limit. The app is marked `PYTHONDONTWRITEBYTECODE=1` so it
does not litter the stick with `.pyc` files.
