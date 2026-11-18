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
import posixpath
import re
import tomllib
from pathlib import Path
from typing import Any, Iterable

from babyai import atomic

ENV_HOME = "BABYAI_HOME"
ENV_PAIRING = "BABYAI_PAIRING"
PAIRING_DIR = "babyai-usb"
PAIRING_NAME = "pairing.toml"
SKIP_DIRS = frozenset({".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})  # (anywhere in the tree)
TOP_SKIPPED = frozenset({"data", "runtime", "ollama"})  # (only at the top: his data, and the bundles covered by TREES)
TREES = ("runtime", "ollama/bin")  # bundled programs: if they are on the stick they run as him, so they are in the manifest
GIT_FILES = (".git/config", ".git/info/attributes")  # what git runs on its own: config (fsmonitor, hooks path...) and hooks
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
    path = _resolve(config_path)
    from_env = os.environ.get(ENV_HOME)
    if from_env:
        # BABYAI_HOME belongs to the config.toml that sits in that folder. Any other config (the tests he runs for a
        # self-upgrade, a one-off `--config`) keeps its own paths: it must never be pointed at the real data on the stick.
        root = _resolve(from_env)
        return root if path.parent == root else None
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
    """SHA-256 for everything on the stick that can run as him: every Python file, the git files that git would run
    (config and hooks), and, if they are there, the bundled Python and Ollama programs (one digest each for the tree)."""
    root = _resolve(root)
    found: dict[str, str] = {}
    for folder, names, files in os.walk(root):
        here = Path(folder)
        top = here == root
        names[:] = sorted(name for name in names if name not in SKIP_DIRS and not (top and name in TOP_SKIPPED))
        for name in sorted(files):
            if name.endswith(TRUST_FILE_SUFFIX):
                _record(found, root, here / name)
    for name in GIT_FILES:
        _record(found, root, root / name)
    hooks = root / ".git" / "hooks"
    if hooks.is_dir():
        for path in sorted(hooks.iterdir()):
            if path.is_file() and not path.name.endswith(".sample"):
                _record(found, root, path)
    for tree in TREES:
        if (root / tree).is_dir():
            found[tree + "/"] = tree_digest(root / tree)
    return found


def _record(found: dict[str, str], root: Path, path: Path) -> None:
    try:
        found[path.relative_to(root).as_posix()] = sha256(path)
    except (OSError, ValueError):
        pass


def tree_digest(folder: Path) -> str:
    """One digest for every file under ``folder`` (names and contents)."""
    digest = hashlib.sha256()
    for path in sorted(item for item in Path(folder).rglob("*") if item.is_file()):
        try:
            digest.update(path.relative_to(folder).as_posix().encode() + b"\0" + sha256(path).encode() + b"\n")
        except OSError:
            digest.update(b"unreadable\n")
    return digest.hexdigest()


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


def refresh_trust(changed: Iterable[str], repo: str | Path | None = None) -> bool:
    """Update the host manifest for files the user just approved and applied.

    ``CodeUpgrades.apply`` and ``VersionUpdates.update`` call this only after the user confirmed the change, and say which
    repository changed: if it is not the portable folder itself (a test run, a scratch copy) nothing is touched. The
    model has no tool that reaches it.
    """
    from_env = os.environ.get(ENV_HOME)
    if not from_env:
        return False
    root = _resolve(from_env)
    if repo is not None and _resolve(repo) != root:
        return False
    path = pairing_path()
    data = load_pairing(path)
    if str(data.get("mode", "")).lower() != "portable":
        return False
    trust = dict(data.get("trust") or {})
    changed_any = False
    for name in changed:
        relative = posixpath.normpath(str(name).replace("\\", "/").strip())
        if relative in ("", ".") or relative.startswith("/") or relative.split("/")[0] == ".." or not relative.endswith(TRUST_FILE_SUFFIX):
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
