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
        "model": "llama-3.1-8b",
        "api_key": "",
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
