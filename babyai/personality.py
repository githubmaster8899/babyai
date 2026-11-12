"""How he sounds: four sliders the user can move, kept on their computer.

The sliders are *manner*, not permissions. They change a few lines in his system prompt
and nothing else: the safety gate never reads them, so being told to be more playful can
never let him skip asking before he acts.

Values are whole numbers from 1 to 5. The starting values come from the ``[personality]``
section of config.toml; changes are kept in ``personality/personality.json`` next to the
memory database, so a moved slider survives a restart without rewriting the user's config.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Mapping

MIN_VALUE, MAX_VALUE, DEFAULT_VALUE = 1, 5, 3

# name -> (one line per value 1..5). Written in the second person: they go into his prompt.
SLIDERS: dict[str, tuple[str, ...]] = {
    "playfulness": (
        "Stay practical and straight; no jokes or teasing.",
        "Mostly straight, with at most a light touch of humour.",
        "A little playful now and then, when it fits.",
        "Be playful; a gentle tease is welcome when it fits.",
        "Be openly playful and mischievous; jokes and gentle teasing are welcome.",
    ),
    "warmth": (
        "Keep an even, reserved tone.",
        "Be friendly, but not effusive.",
        "Be warm and friendly.",
        "Be warm and openly fond of them.",
        "Be affectionate and openly caring; comfort comes first.",
    ),
    "curiosity": (
        "Don't ask follow-up questions unless you need to.",
        "Ask a follow-up only when it really helps.",
        "Show some curiosity; ask now and then.",
        "Be curious: ask about what they're making and how it's going.",
        "Ask plenty of genuine follow-ups; you love hearing about their day.",
    ),
    "verbosity": (
        "One short sentence.",
        "One or two short sentences.",
        "Two or three short sentences.",
        "Three or four short sentences when the answer needs it.",
        "Take the room you need, up to a few sentences; never a wall of text.",
    ),
}

HEADER = "Your manner right now (sliders the user can move; stay within your rules):"


def clamp(value: Any, default: int = DEFAULT_VALUE) -> int:
    """A whole number from 1 to 5, or ``default`` for anything else (a typo can never break him)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return min(MAX_VALUE, max(MIN_VALUE, value))


def defaults_from_config(section: Mapping[str, Any] | None) -> dict[str, int]:
    section = section if isinstance(section, Mapping) else {}
    return {name: clamp(section.get(name, DEFAULT_VALUE)) for name in SLIDERS}


def _fresh(values: Mapping[str, int]) -> dict[str, Any]:
    return {"version": 1, "values": {name: clamp(values.get(name, DEFAULT_VALUE)) for name in SLIDERS}}


def _valid(state: Any) -> bool:
    try:
        return (
            state["version"] == 1
            and set(state["values"]) == set(SLIDERS)
            and all(isinstance(value, int) and not isinstance(value, bool) and MIN_VALUE <= value <= MAX_VALUE
                    for value in state["values"].values())
        )
    except (KeyError, TypeError, AttributeError):
        return False


class Personality:
    """The four sliders, their prompt text, and the controls the user reaches through the tool."""

    def __init__(self, directory: str | Path, defaults: Mapping[str, int] | None = None):
        self.directory = Path(os.path.expanduser(str(directory)))
        self.path = self.directory / "personality.json"
        self.defaults = {name: clamp((defaults or {}).get(name, DEFAULT_VALUE)) for name in SLIDERS}
        self._lock = threading.RLock()
        self.state = self._load()

    # ------------------------------------------------------------------ the file

    def _load(self) -> dict[str, Any]:
        try:
            state = json.loads(self.path.read_text())
        except (OSError, ValueError):
            state = None
        return state if _valid(state) else _fresh(self.defaults)

    def _save(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(self.path.name + ".tmp")
            temp.write_text(json.dumps(self.state, indent=1, sort_keys=True))
            os.replace(temp, self.path)
        except OSError:
            pass  # a slider must never get in the way of an answer

    # ------------------------------------------------------------------ using it

    def values(self) -> dict[str, int]:
        with self._lock:
            return dict(self.state["values"])

    def prompt_block(self) -> str:
        """The manner lines that go into his system prompt, or "" while every slider is at its neutral 3.

        Only moved sliders are described: the starting values add nothing the normal prompt doesn't already say, so
        an untouched personality leaves the prompt exactly as it was."""
        with self._lock:
            values = dict(self.state["values"])
        moved = [(name, value) for name, value in values.items() if value != DEFAULT_VALUE]
        if not moved:
            return ""
        lines = [f"- {name}: {SLIDERS[name][value - 1]}" for name, value in moved]
        return "\n" + "\n".join([HEADER, *lines])

    def show(self) -> str:
        with self._lock:
            values = dict(self.state["values"])
        lines = ["My manner sliders (1 is quietest, 5 is strongest; you can change them):"]
        for name, value in values.items():
            bar = "#" * value + "-" * (MAX_VALUE - value)
            lines.append(f"- {name}: {value}/{MAX_VALUE} [{bar}] {self.label(name, value)}")
        lines.append(f"They live in one file you can open or delete: {self.path}.")
        return "\n".join(lines)

    @staticmethod
    def label(name: str, value: int) -> str:
        """A one-word summary of a slider position, for the show output."""
        words = {
            "playfulness": ("solemn", "dry", "balanced", "playful", "mischievous"),
            "warmth": ("reserved", "polite", "friendly", "fond", "affectionate"),
            "curiosity": ("incurious", "restrained", "curious", "interested", "nosy"),
            "verbosity": ("terse", "brief", "balanced", "fuller", "expansive"),
        }
        return words.get(name, ("", "", "balanced", "", ""))[clamp(value) - 1]

    # ------------------------------------------------------------------ what the user can do

    def set(self, slider: str, value: int) -> str:
        name = (slider or "").strip().lower()
        if name not in SLIDERS:
            raise ValueError(f"{slider!r} isn't one of my sliders. They are: {', '.join(SLIDERS)}.")
        number = clamp(value, DEFAULT_VALUE) if isinstance(value, int) and not isinstance(value, bool) else None
        if number is None:
            raise ValueError(f"Give {name} a whole number from {MIN_VALUE} to {MAX_VALUE}.")
        with self._lock:
            self.state["values"][name] = number
            self._save()
        return f"{name} is now {number}/{MAX_VALUE}: {SLIDERS[name][number - 1]}"

    def reset(self) -> str:
        with self._lock:
            self.state = _fresh(self.defaults)
            self._save()
        return "Personality sliders back to their starting values."

    def preview(self, action: str, slider: str = "", value: int = 0) -> dict[str, Any] | None:
        name = (action or "").strip().lower()
        if name == "set":
            shown = (slider or "").strip().lower()
            if shown in SLIDERS:
                return {"personality": f"{shown}: {clamp(value)}/{MAX_VALUE} ({self.label(shown, clamp(value))})"}
            return {"personality": "change a slider"}
        if name == "reset":
            return {"personality": "reset every slider to its starting value"}
        return None
