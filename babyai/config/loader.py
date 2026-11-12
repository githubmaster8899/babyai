import os
from copy import deepcopy
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "base_url": "http://localhost:11434/v1",
        "model": "llama3.1:8b",  # an Ollama tag that supports tools; run `ollama list` to see what you have
        "api_key": "",
        "timeout": 120,  # seconds; the first request can be slow while Ollama loads the model
        "vision_model": "",  # for looking at the screen (llava, qwen2.5vl, ...): empty means he says he needs one
    },
    "agent": {
        "name": "BabyAI",
        "system_prompt": "You are BabyAI, a local-first private assistant. Be concise and helpful.",
    },
    "upgrade": {
        "enabled": True,  # he can upgrade himself (code on a branch, updates, models, notes); every change needs your yes
        "dir": "~/.local/share/babyai/self",  # his working branches, journal, notes and skills
    },
    "ideas": {
        "enabled": True,  # he thinks about how to get better and suggests it (he only suggests: nothing changes until you say yes)
        "min_hours": 24,  # at most one idea put to you per this many hours
        "check_updates": True,  # once a day, when he starts, a read-only git fetch to see if a newer version exists
    },
    "habits": {
        "enabled": True,  # he learns your habits (what you ask, when, how you like replies); counts only, you can see/pause/wipe them
        # "dir" is a habits folder next to the memory database unless you set it here
    },
    "personality": {
        "enabled": True,  # four manner sliders (1..5) that shape how he talks; you can change them by asking, and they are
        # kept in one file next to the memory database. The safety gate never reads them.
        "playfulness": 3,
        "warmth": 3,
        "curiosity": 3,
        "verbosity": 3,
        # "dir" is a personality folder next to the memory database unless you set it here
    },
    "memory": {
        "db_path": "~/.local/share/babyai/memory.db",
    },
    # Start him when a certain USB drive is plugged in and close him when it is taken out (Linux; `python3 -m babyai.usbwatch pair`
    # picks the drive and sets "drive" for you). Nothing on the drive is read or run: it is only a key.
    "usb": {
        "drive": "",  # e.g. "uuid:ABCD-1234" - empty until you pair a drive
        "launch_if_present": True,  # the drive is already in when the watcher starts (e.g. at login): start him then too
        "close_on_remove": True,
        "grace_seconds": 3,  # the drive has to be gone this long before he is closed, so a flaky connection doesn't close him
    },
    "voice": {
        "enabled": False,
        "wakeword": "baby",
        "wakeword_model": "",
        "stt_model": "base",
        "tts_voice": "",
    },
    "safety": {
        "level": "strict",
    },
    # Desktop overlay (used by `python3 -m babyai --desktop`); 0 turns auto-sleep off.
    # character: "capri" = Capri's sprites + persona, "stick" = the plain stick figure.
    # sprite_scale: Tk can only scale by simple fractions - "1/2", "2/3", "3/4" or "1".
    # typing: where your keys are caught - "auto" (a small box on Wayland, where the borderless overlay can't take
    #   keyboard focus; her bubble elsewhere), "box" (always the box) or "bubble" (never).
    "ui": {
        "character": "capri",
        "sprite_scale": "1/2",
        "typing": "auto",
        "think_after_s": 15,
        "sleep_after_s": 600,
        "wake_radius_px": 150,
        "look_radius_px": 250,
        "bubble_hide_s": 10,  # her speech bubble hides this long after she finishes talking; 0 keeps it up
        "hide_and_seek": True,  # after a long quiet spell she slips behind the screen edge, peeks back now and then,
        # and returns the moment you click, type or she has something to say. false keeps her where she is.
        "hide_after_s": 180,  # idle seconds before she hides (0 hides as soon as she has finished talking)
        "peek_every_s": 12,  # while hidden, how often she peeks back out
        "peek_for_s": 2.5,  # how long one peek lasts
        "sounds": False,  # little sounds (wake chime, reply blip, a knock when she asks permission, a chirp when petted); needs pw-play, paplay or aplay
        "sound_volume": 0.4,  # 0 to 1
        "summon": True,  # listen for `python3 -m babyai --toggle|--show|--hide` (bind --toggle to a keyboard shortcut to bring her up or send her away)
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str = "config.toml") -> dict[str, Any]:
    config = deepcopy(DEFAULT_CONFIG)
    resolved_path = os.path.expanduser(path)
    if not os.path.exists(resolved_path):
        return config
    with open(resolved_path, "rb") as handle:
        loaded = tomllib.load(handle)
    return _deep_merge(config, loaded)
