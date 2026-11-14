"""Where a portable BabyAI keeps its code, configuration and data.

A portable copy lives on the USB stick as ``<root>/babyai``, ``<root>/capri``,
``<root>/assets`` and ``<root>/config.toml``.  Its data goes under
``<root>/data``.  The host launcher sets ``BABYAI_HOME``; ``--portable`` and a
``[usb] mode = "portable"`` config next to the app do the same by hand.

Only paths are redirected.  ``HOME`` is not changed, and nothing in this module
writes under it.  The launcher's log and pairing record are the only host-side
files, and they belong to the launcher.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any, Iterable

from babyai import atomic

ENV_HOME = "BABYAI_HOME"
ENV_PAIRING = "BABYAI_PAIRING"
PAIRING_DIR = "babyai-usb"
PAIRING_NAME = "pairing.toml"
SKIP_PARTS = frozenset({"data", "runtime", "ollama", ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
TRUST_FILE_SUFFIX = ".py"


def _resolve(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def app_root(config_path: str | Path) -> Path:
    return _resolve(config_path).parent


def is_app_root(path: str | Path) -> bool:
    root = _resolve(path)
    return (root / "babyai" / "__init__.py").is_file()


def root_for(config: dict[str, Any], config_path: str | Path) -> Path | None:
    """The portable root, if this run is portable, otherwise None."""
    from_env = os.environ.get(ENV_HOME)
    if from_env:
        return _resolve(from_env)
    path = _resolve(config_path)
    mode = str(config.get("usb", {}).get("mode", "")).lower()
    if mode == "portable" and is_app_root(path.parent):
        return path.parent
    return None


def apply_paths(config: dict[str, Any], root: str | Path) -> dict[str, Any]:
    """Point every default data path at ``<root>/data``."""
    data = _resolve(root) / "data"
    config.setdefault("memory", {})["db_path"] = str(data / "memory.db")
    config.setdefault("upgrade", {})["dir"] = str(data / "self")
    config.setdefault("habits", {})["dir"] = str(data / "habits")
    config.setdefault("personality", {})["dir"] = str(data / "personality")
    if isinstance(config.get("reminders"), dict):  # (a later feature: redirect it when it exists)
        config["reminders"]["dir"] = str(data / "reminders")
    config.setdefault("usb", {})["mode"] = "portable"
    return config


def prepare_environment(root: str | Path) -> Path:
    """Create the data folders and point the caches there.  Never touches HOME."""
    root = _resolve(root)
    data = root / "data"
    for name in ("", "cache", "habits", "personality", "reminders", "self"):
        (data / name).mkdir(parents=True, exist_ok=True)
    os.environ[ENV_HOME] = str(root)
    os.environ["XDG_CACHE_HOME"] = str(data / "cache")
    return root


def pairing_path() -> Path:
    override = os.environ.get(ENV_PAIRING)
    if override:
        return _resolve(override)
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return _resolve(base) / PAIRING_DIR / PAIRING_NAME


def load_pairing(path: str | Path | None = None) -> dict[str, Any]:
    target = _resolve(path) if path is not None else pairing_path()
    try:
        with open(target, "rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def save_pairing(data: dict[str, Any], path: str | Path | None = None) -> None:
    target = _resolve(path) if path is not None else pairing_path()
    atomic.write_text(target, toml_dump(data))


def _toml_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value))


def toml_dump(data: dict[str, Any]) -> str:
    """A small writer for the flat pairing record (scalars plus one level of tables)."""
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


def manifest(root: str | Path) -> dict[str, str]:
    """SHA-256 for every Python file that can run from this app folder."""
    root = _resolve(root)
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*" + TRUST_FILE_SUFFIX)):
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if any(part in SKIP_PARTS for part in relative.split("/")):
            continue
        try:
            found[relative] = sha256(path)
        except OSError:
            continue
    return found


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def trust_changes(pairing: dict[str, Any], root: str | Path) -> list[str]:
    """Every Python file whose contents differ from the trusted manifest."""
    expected = pairing.get("trust") or {}
    current = manifest(root)
    return sorted(name for name in set(expected) | set(current) if expected.get(name) != current.get(name))


def refresh_trust(changed: Iterable[str]) -> bool:
    """Update the host manifest for files the user just approved and applied.

    ``CodeUpgrades.apply`` and ``VersionUpdates.update`` call this only after
    the user confirmed the change.  The model has no tool that reaches it.
    """
    from_env = os.environ.get(ENV_HOME)
    if not from_env:
        return False
    root = _resolve(from_env)
    path = pairing_path()
    data = load_pairing(path)
    if str(data.get("mode", "")).lower() != "portable":
        return False
    trust = dict(data.get("trust") or {})
    changed_any = False
    for name in changed:
        relative = str(name).replace("\\", "/").lstrip("./")
        if not relative or relative.startswith("../") or not relative.endswith(TRUST_FILE_SUFFIX):
            continue
        target = root / relative
        if target.is_file():
            try:
                trust[relative] = sha256(target)
            except OSError:
                continue
        else:
            trust.pop(relative, None)
        changed_any = True
    if changed_any:
        data["trust"] = trust
        save_pairing(data, path)
    return changed_any
