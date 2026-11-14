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
| Changes on the stick checked? | no | SHA-256 manifest of every `.py` |

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
* records the SHA-256 manifest and installs/starts the host watcher.

Useful options: `--data-from PATH`, `--no-data`, `--overwrite-data`,
`--app-folder NAME`, `--uuid UUID`, `--no-install`, and `--yes` (only when you
are certain). The default app folder is `BabyAI`; the mount point may contain
spaces.

The launcher resolves the stick by filesystem UUID, waits about 20 seconds for
the desktop to automount it, and only then falls back to `udisksctl mount`. It
checks the display, Python version, Tkinter, and whether the drive is mounted
`noexec` (bundled runtime/Ollama programs cannot run from a `noexec` mount).

## Trusting changes on a portable stick

A portable pairing includes a SHA-256 manifest of every `.py` file under the app
folder (excluding `.git`, `data`, `runtime`, `ollama` and caches). Before
launch, the launcher recomputes it. If anything is new, changed or missing, it:

* logs the mismatch and sends a desktop notification;
* refuses to launch;
* tells you to run `trust`.

The `trust` command lists the changed files and asks before updating the
manifest:

```sh
# from the app folder on the stick, or with the package on PYTHONPATH
python3 -m babyai.usbwatch trust
```

`trust` is a command-line action only. The model has no tool that can run it,
and the launcher files and trust tests are on the protected list. A failed
manifest check is never retried in a loop while the stick stays plugged in;
unplug and replug after trusting.

Changes the user approves through the existing self-upgrade flow
(`upgrade_code apply`, `upgrade_version update`, rollback) refresh the manifest
at the end of that operation, because the user already approved those files.
A manual edit on the stick still needs `trust`.

## Removing the stick safely

Use the right-click **Eject USB** menu in the overlay. It:

1. verifies the pid is the BabyAI instance for this drive before stopping it;
2. stops the bundled Ollama if the current watcher started one;
3. stops the watcher;
4. runs `udisksctl unmount -b <device>`, retrying for a short while if the
   desktop is still busy.

If `udisksctl` is missing or the drive stays busy, close anything else using
the stick and use the desktop's **Safely Remove**. Do not pull a portable stick
while the app is writing. The memory database uses `journal_mode=DELETE` (no
`-wal` file), `synchronous=FULL`, an integrity check at startup, and atomic
writes for the JSON state files. Disk I/O errors from a removal are swallowed,
remembered by the memory tool, and the watcher closes the app after the grace
period.

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

## What still needs real-drive testing

The test suite covers the fake `/dev/disk`, fake mountinfo, trust changes,
copying, config rewriting, SQLite settings and atomic writes. These need a real
stick and a desktop session:

* a fresh FAT32 stick through `make-drive` and `trust`;
* plug, unplug, and replug with an app already running;
* remembering a fact and setting a reminder while the app is up;
* pulling the stick mid-use and checking the app exits and the database is
  readable on the next mount;
* KDE's Safely Remove reporting the drive busy, then the Eject USB menu;
* logout/login with the stick in, and a session with no stick;
* editing a `.py` on the stick and confirming the launch refusal and `trust`;
* confirming `~/.local/share/babyai` and `~/.cache/babyai` are unchanged while
  the portable copy runs.

FAT32 has no real Unix execute bits, fakes ownership, is case-insensitive and
has a 4 GB per-file limit. The app is marked `PYTHONDONTWRITEBYTECODE=1` so it
does not litter the stick with `.pyc` files.
