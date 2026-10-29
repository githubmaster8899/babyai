from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _capri_candidates() -> list[Path]:
    root = Path(__file__).resolve().parent.parent
    candidates = [
        root / "assets" / "Capri" / "Capri",
        root / "assets" / "Capri-linux-x64.tar.gz",
    ]
    return candidates


def launch_capri() -> bool:
    """Launch the packaged Capri binary if present.

    The repo can contain either the extracted ELF binary at
    ``assets/Capri/Capri`` or the original archive at
    ``assets/Capri-linux-x64.tar.gz``. If the archive exists, this helper will
    extract it to ``assets/Capri`` and then launch the binary.
    """
    root = Path(__file__).resolve().parent.parent
    exec_path = root / "assets" / "Capri" / "Capri"
    archive_path = root / "assets" / "Capri-linux-x64.tar.gz"

    if not exec_path.exists() and archive_path.exists():
        extract_dir = root / "assets"
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                ["tar", "-xzf", str(archive_path), "-C", str(extract_dir)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError):
            return False

    if not exec_path.exists():
        return False

    if not os.access(exec_path, os.X_OK):
        try:
            exec_path.chmod(exec_path.stat().st_mode | 0o111)
        except OSError:
            return False

    try:
        subprocess.Popen(
            [str(exec_path)],
            cwd=str(root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        return True
    except OSError:
        return False
