"""Loads Capri's sprite strips into Tk and hands out single frames.

Capri's sprites (``capri/assets/sprites/*.png``) are her art, used under the PolyForm
Noncommercial License 1.0.0 - see ``capri/LICENSE.md`` and the root ``NOTICE``.
Required Notice: Copyright 2026 Lachy (https://github.com/lachydotmcg)

Two practical details:

* Tk can only scale by whole ratios (zoom / subsample), so the scale is a small fraction
  such as ``1/2`` or ``3/4``.
* Tk decodes these PNGs slowly (3-5 s for the long strips). The first time a strip is used it
  is scaled and written to a cache folder in Tk's own encoding, which reloads in ~0.15 s, so
  only the very first run pays for it. The cache never touches ``capri/``.
"""

from __future__ import annotations

import os
import tkinter as tk
from fractions import Fraction
from pathlib import Path

from babyai.ui.capri_body import CLIPS

SPRITE_DIR = Path(__file__).resolve().parent.parent.parent / "capri" / "assets" / "sprites"
REQUIRED = ("idle", "blink", "mouth")  # without these there is no figure worth drawing
DEFAULT_SCALE = "1/2"


def parse_scale(value: object) -> tuple[int, int]:
    """'1/2', '3/4', 0.5, ... -> (zoom, subsample). Clamped to 1/4..1 and to a denominator of at most 4."""
    try:
        fraction = Fraction(str(value)).limit_denominator(4)
    except (ValueError, ZeroDivisionError):
        fraction = Fraction(DEFAULT_SCALE)
    fraction = min(Fraction(1), max(Fraction(1, 4), fraction))
    return fraction.numerator, fraction.denominator


def cache_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "babyai" / "sprites"


class SpriteBank:
    def __init__(self, root: tk.Misc, directory: Path | str | None = None, scale: object = DEFAULT_SCALE, cache: Path | str | None = None):
        self.root = root
        self.directory = Path(directory) if directory else SPRITE_DIR
        self.zoom, self.sub = parse_scale(scale)
        self._cache_dir = Path(cache) if cache is not None else cache_root()
        self._strips: dict[str, tk.PhotoImage | None] = {}
        self._cells: dict[tuple[str, int], tk.PhotoImage] = {}

    # ------------------------------------------------------------------ loading

    def available(self) -> bool:
        return all((self.directory / f"{name}.png").is_file() for name in REQUIRED)

    def _cache_path(self, name: str) -> Path | None:
        source = self.directory / f"{name}.png"
        try:
            stat = source.stat()
        except OSError:
            return None
        key = f"{self.zoom}-{self.sub}-{stat.st_size}-{stat.st_mtime_ns}"
        return self._cache_dir / key / f"{name}.png"

    def cached(self, names: tuple[str, ...]) -> bool:
        """True if every one of these clips is already in the cache (so loading will be quick)."""
        paths = [self._cache_path(name) for name in names]
        return all(path is not None and path.is_file() for path in paths)

    def strip(self, name: str) -> tk.PhotoImage | None:
        """The scaled strip for a clip, or None if it can't be loaded (callers then skip that clip)."""
        if name not in self._strips:
            self._strips[name] = self._load(name)
        return self._strips[name]

    def _load(self, name: str) -> tk.PhotoImage | None:
        cached = self._cache_path(name)
        if cached is not None and cached.is_file():
            try:
                return tk.PhotoImage(master=self.root, file=str(cached))
            except tk.TclError:
                pass  # a damaged cache file: rebuild it below
        try:
            big = tk.PhotoImage(master=self.root, file=str(self.directory / f"{name}.png"))
        except tk.TclError:
            return None
        if (self.zoom, self.sub) == (1, 1):
            strip = big
        else:
            strip = tk.PhotoImage(master=self.root)
            self.root.tk.call(strip, "copy", big, "-zoom", self.zoom, "-subsample", self.sub)
        if cached is not None:
            try:
                cached.parent.mkdir(parents=True, exist_ok=True)
                temp = cached.with_name(f"{name}.{os.getpid()}.tmp")
                strip.write(str(temp), format="png")
                os.replace(temp, cached)
            except (OSError, tk.TclError):
                pass  # read-only cache: fine, it just stays slow next time
        return strip

    def preload(self, names: tuple[str, ...]) -> list[str]:
        """Load these clips now; returns the ones that could not be loaded."""
        return [name for name in names if self.strip(name) is None]

    # ------------------------------------------------------------------- frames

    @property
    def cell_size(self) -> tuple[int, int]:
        idle = self.strip("idle")
        if idle is None:
            return (0, 0)
        return (idle.width() // CLIPS["idle"].cells, idle.height())

    def cell(self, name: str, index: int) -> tk.PhotoImage | None:
        key = (name, index)
        if key not in self._cells:
            strip = self.strip(name)
            if strip is None:
                return None
            width = strip.width() // CLIPS[name].cells
            image = tk.PhotoImage(master=self.root)
            self.root.tk.call(image, "copy", strip, "-from", index * width, 0, (index + 1) * width, strip.height())
            self._cells[key] = image
        return self._cells[key]

    def is_opaque(self, name: str, index: int, x: int, y: int) -> bool:
        """Is the pixel at (x, y) of this frame part of her (not see-through)?"""
        image = self.cell(name, index)
        if image is None or not (0 <= x < image.width() and 0 <= y < image.height()):
            return False
        return not image.transparency_get(x, y)
