"""Start the Capri desktop companion (the C# app under ``capri/``) as its own process.

BabyAI never imports Capri. Capri is a separate app with its own brain and
settings; this module only finds a compiled Capri binary and starts it,
detached, so it keeps running after BabyAI exits. Capri's license
(PolyForm Noncommercial) is in ``capri/LICENSE.md`` and summarised in ``NOTICE``.

Build Capri once, on a machine with the .NET 10 SDK (see ``BUILD_HELP``).
To use a binary from somewhere else, set ``BABYAI_CAPRI_PATH`` to the
executable, or to the folder that contains it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ENV_VAR = "BABYAI_CAPRI_PATH"
CAPRI_DIR = Path(__file__).resolve().parent.parent / "capri"
EXE_NAME = "Capri.exe" if os.name == "nt" else "Capri"

BUILD_HELP = f"""Capri has not been built yet. On a machine with the .NET 10 SDK:
  Linux / macOS:  cd capri && dotnet build cross/Capri.Cross.csproj -c Release -o out/cross
  Windows:        cd capri; ./ci/build-windows.ps1 -SmokeTest
Or set {ENV_VAR} to an existing Capri executable."""


@dataclass
class LaunchResult:
    ok: bool
    message: str
    process: subprocess.Popen | None = None


def _default_candidates() -> list[Path]:
    out = CAPRI_DIR / "out"
    candidates = [out / "cross" / EXE_NAME, out / "Capri" / EXE_NAME]  # dotnet build / upstream publish layout
    if os.name == "nt":
        candidates.append(out / "windows" / "Capri.exe")  # ci/build-windows.ps1
    return candidates


def find_capri() -> Path | None:
    """Return the Capri executable to launch, or None if there isn't one.

    If BABYAI_CAPRI_PATH is set it is the only place checked, so a wrong
    path is reported instead of silently falling back to another build.
    """
    override = os.environ.get(ENV_VAR, "").strip()
    if override:
        path = Path(override).expanduser()
        if path.is_dir():
            path = path / EXE_NAME
        return path if path.is_file() else None
    for candidate in _default_candidates():
        if candidate.is_file():
            return candidate
    return None


def _missing_display() -> bool:
    return sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def launch_capri(binary: Path | str | None = None, settle_seconds: float = 1.5) -> LaunchResult:
    """Start Capri detached. Never raises; failures come back as ``ok=False`` with a reason."""
    path = Path(binary) if binary else find_capri()
    if path is None or not path.is_file():
        if os.environ.get(ENV_VAR, "").strip() and not binary:
            return LaunchResult(False, f"{ENV_VAR} is set to '{os.environ[ENV_VAR]}', but no Capri executable was found there.")
        return LaunchResult(False, BUILD_HELP)
    if _missing_display():
        return LaunchResult(False, "Capri needs a graphical desktop (neither DISPLAY nor WAYLAND_DISPLAY is set).")
    if os.name != "nt" and not os.access(path, os.X_OK):
        return LaunchResult(False, f"{path} is not executable. Try: chmod +x '{path}'")

    options: dict = {"cwd": str(path.parent), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    else:
        options["start_new_session"] = True
        options["close_fds"] = True
    try:
        process = subprocess.Popen([str(path)], **options)
    except OSError as exc:
        return LaunchResult(False, f"Could not start Capri ({path}): {exc}")

    try:
        code = process.wait(timeout=settle_seconds)
    except subprocess.TimeoutExpired:
        return LaunchResult(True, f"Capri started (pid {process.pid}).", process)
    return LaunchResult(False, f"Capri exited immediately with code {code}. Run '{path}' in a terminal to see why.")
