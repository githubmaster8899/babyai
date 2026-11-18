# BabyAI

BabyAI is a local-first, voice-friendly desktop companion: a floating Tkinter
overlay with Capri's body and BabyAI's memory, tools and safety gate. It runs
on Linux, keeps its data under your home folder by default, and can use a local
or remote OpenAI-compatible model server.

## Running it

Install the Python dependencies from `pyproject.toml`, make a `config.toml`
beside the checkout (see `config.toml` and the defaults in
`babyai/config/loader.py`), then:

```sh
python3 -m babyai --desktop
```

The command line works too (`python3 -m babyai "what do you remember?"`), and
the test suite runs without a display or network:

```sh
python3 -m unittest discover -s tests -t .
```

## USB drive

There are two ways to use a USB stick. Both are optional and Linux-only; the
default remains an ordinary checkout on the computer.

**Key mode** keeps the app on the computer and uses the stick only as a key:
plugging it in starts BabyAI, taking it out closes it, and nothing is read from
or written to the stick. Pair once with:

```sh
python3 -m babyai.usbwatch pair
```

**Portable mode** puts the app, Capri assets, config and all data on the stick.
The stick can then be moved between machines with the same Linux user name and
a Python 3.11+ with Tk:

```sh
# with the stick mounted at /run/media/you/BENNY, and from this checkout
python3 -m babyai.usbwatch make-drive /run/media/you/BENNY
```

`make-drive` shows `findmnt`, asks before writing, refuses a non-empty
`BabyAI/` unless `--force`, copies the data under `~/.local/share/babyai`, and
records the drive's filesystem UUID rather than its mount path. A portable
drive is paired with a SHA-256 manifest of every `.py` in its app folder (and its
git settings and any bundled programs). If a file changes, the launcher refuses to start and lists the files; only the
person at the machine can accept them, in a real terminal:

```sh
python3 -m babyai.usbwatch trust
```

Use the right-click **Eject USB** menu before pulling a portable drive. It
closes the app and stops the bundled Ollama (if one was started), then asks
`udisksctl` to unmount the drive. If `udisksctl` is not available, use the
desktop's Safely Remove instead.

The host launcher lives in `~/.local/share/babyai-usb/usbwatch.py` and its
pairing record in `~/.config/babyai-usb/pairing.toml` (honouring
`XDG_DATA_HOME`/`XDG_CONFIG_HOME`). It is deliberately standalone: it imports
nothing from `babyai`, so it keeps working when the drive is gone.

See [docs/USB.md](docs/USB.md) for the full commands, safety rules, and the
things that still need testing on real hardware.

## Licence

BabyAI's own code is under the licence in `LICENSE`; see `NOTICE`. Capri's
licence is in `capri/LICENSE.md` and must be kept with it.
