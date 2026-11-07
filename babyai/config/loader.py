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
    },
    "agent": {
        "name": "BabyAI",
        "system_prompt": "You are BabyAI, a local-first private assistant. Be concise and helpful.",
    },
    "memory": {
        "db_path": "~/.local/share/babyai/memory.db",
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
