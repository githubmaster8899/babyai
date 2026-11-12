"""Little sounds for the overlay: a soft chime when she wakes up, a blip when a reply is ready, a two-note "may I?" when she asks
permission, a happy chirp when she is petted.

They are made here out of sine waves with the standard library (a few KB each, written once to the cache folder) and played with
whichever of ``pw-play`` / ``paplay`` / ``aplay`` / ``afplay`` is installed. Nothing is installed or downloaded, and nothing is
played unless ``[ui] sounds = true``. Display-free, so it can be tested without Tk.
"""

from __future__ import annotations

import io
import math
import os
import shutil
import struct
import subprocess
import time
import wave
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping

RATE = 22050
# (frequency in Hz, seconds) one after the other; a frequency of 0 is a rest
SOUNDS: dict[str, tuple[tuple[float, float], ...]] = {
    "wake": ((523.25, 0.11), (659.25, 0.11), (783.99, 0.2)),  # C E G, going up
    "reply": ((880.0, 0.07), (1174.66, 0.1)),
    "ask": ((659.25, 0.12), (0, 0.04), (659.25, 0.16)),  # two of the same note: a knock
    "pet": ((784.0, 0.06), (1046.5, 0.06), (1318.5, 0.12)),
}
PLAYERS = (("pw-play",), ("paplay",), ("aplay", "-q"), ("afplay",))  # first one found is used
MIN_GAP_S = 0.25  # the same sound is not started again within this long (a stroked pointer would otherwise rattle)
MAX_AT_ONCE = 3
DEFAULT_VOLUME = 0.4
EDGE_S = 0.012  # each note fades in and out over this long, so there is no click between notes


def clamp_volume(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(value):
        return DEFAULT_VOLUME
    return max(0.0, min(1.0, float(value)))


@lru_cache(maxsize=32)
def make_wav(name: str, volume: float = DEFAULT_VOLUME) -> bytes:
    """The named sound as a mono 16-bit WAV file (the same bytes every time)."""
    peak = int(32767 * 0.8 * clamp_volume(volume))
    samples: list[int] = []
    for frequency, seconds in SOUNDS[name]:
        count = int(RATE * seconds)
        edge = max(1, int(RATE * EDGE_S))
        for i in range(count):
            fade = min(1.0, i / edge, (count - 1 - i) / edge)
            samples.append(int(peak * fade * math.sin(2 * math.pi * frequency * i / RATE)) if frequency else 0)
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return out.getvalue()


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "babyai" / "sounds"


class SoundPlayer:
    def __init__(
        self, enabled: bool = False, volume: float = DEFAULT_VOLUME, directory: Path | None = None,
        find: Callable[[str], str | None] = shutil.which, run: Callable[..., Any] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.enabled = bool(enabled)
        self.volume = clamp_volume(volume)
        self._directory = directory
        self._find, self._run, self._clock = find, run, clock
        self._last: dict[str, float] = {}
        self._playing: list[Any] = []
        self._broken = False

    @classmethod
    def from_config(cls, ui: Mapping[str, Any] | None, **options: Any) -> "SoundPlayer":
        ui = ui or {}
        return cls(enabled=ui.get("sounds", False) is True, volume=ui.get("sound_volume", DEFAULT_VOLUME), **options)

    def _command(self) -> list[str] | None:
        for name, *flags in PLAYERS:
            found = self._find(name)
            if found:
                return [found, *flags]
        return None

    def _file(self, name: str) -> Path:
        directory = self._directory or cache_dir()
        path = directory / f"{name}-{int(self.volume * 100)}.wav"
        data = make_wav(name, self.volume)
        if not path.exists() or path.stat().st_size != len(data):  # (made again if it is missing or damaged)
            directory.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(path.name + ".tmp")
            temp.write_bytes(data)
            os.replace(temp, path)
        return path

    def play(self, name: str) -> bool:
        """Start the sound and return at once. True if it was started; never raises (a sound is not worth a crash)."""
        if not self.enabled or self._broken or self.volume <= 0 or name not in SOUNDS:
            return False
        now = self._clock()
        if now - self._last.get(name, -MIN_GAP_S) < MIN_GAP_S:
            return False
        self._playing = [p for p in self._playing if p.poll() is None]  # (reaps the ones that have finished)
        if len(self._playing) >= MAX_AT_ONCE:
            return False
        command = self._command()
        if command is None:
            return False
        try:
            path = self._file(name)
            self._playing.append(self._run([*command, str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        except OSError:  # no cache folder to write to, or the player will not start: stay quiet from now on
            self._broken = True
            return False
        self._last[name] = now
        return True
