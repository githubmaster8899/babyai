"""Small writes that survive a yanked USB stick.

A JSON or text file is written to a temporary file beside it, flushed to the
disk, and only then renamed over the old one.  A reader therefore sees either
the old file or the new one, never half of each.  ``fsync`` is the part that
matters on a removable drive: without it the rename can reach the drive before
the bytes do.
"""

from __future__ import annotations

import os
from pathlib import Path


def write_text(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    with open(temp, "w", encoding=encoding, newline="") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, target)
    _sync_directory(target.parent)


def write_bytes(path: str | Path, data: bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    with open(temp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, target)
    _sync_directory(target.parent)


def _sync_directory(directory: Path) -> None:
    """Ask the filesystem to persist the rename too (where it supports that)."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass  # FAT and some network filesystems do not fsync a directory
    finally:
        os.close(descriptor)
