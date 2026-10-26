from __future__ import annotations

from pathlib import Path


def ensure_directory(path: str | Path) -> Path:
    target = Path(path).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    return target


def safe_read_text(path: str | Path, encoding: str = "utf-8") -> str:
    return Path(path).expanduser().read_text(encoding=encoding)
