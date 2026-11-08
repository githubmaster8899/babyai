"""The plumbing the self-upgrade workflows share: running git, the journal of what was applied, running the tests.

Everything here raises ``UpgradeError`` with a message written for the model (and so for the user) rather than a
traceback; the tools turn it into a plain reply.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

GIT_TIMEOUT_S = 60
TEST_TIMEOUT_S = 600
IDENTITY = ("-c", "user.name=BabyAI", "-c", "user.email=babyai@localhost")  # so commits never need the user's git identity


class UpgradeError(Exception):
    """A refusal or failure to explain to the user in plain words."""


def run(args: Sequence[str], cwd: Path | str, timeout: float = GIT_TIMEOUT_S, env: dict[str, str] | None = None) -> tuple[int, str]:
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", **(env or {})}
    try:
        done = subprocess.run(list(args), cwd=str(cwd), capture_output=True, text=True, timeout=timeout, env=environment)
    except FileNotFoundError:
        raise UpgradeError(f"{args[0]} isn't installed here") from None
    except subprocess.TimeoutExpired:
        raise UpgradeError(f"{' '.join(args[:2])} took longer than {int(timeout)} seconds, so I stopped it") from None
    return done.returncode, (done.stdout + done.stderr).strip()


def git(repo: Path | str, *args: str, timeout: float = GIT_TIMEOUT_S, check: bool = True) -> str:
    code, output = run(["git", *args], repo, timeout)
    if check and code != 0:
        raise UpgradeError(f"git {args[0]} failed: {output[-300:] or 'no details'}")
    return output


def short(sha: str) -> str:
    return sha[:8]


def is_clean(repo: Path) -> bool:
    """No uncommitted changes to tracked files (untracked files such as config.toml don't count)."""
    return not git(repo, "status", "--porcelain", "--untracked-files=no")


def require_clean(repo: Path) -> None:
    if not is_clean(repo):
        raise UpgradeError("There are uncommitted changes in my folder, so I won't change it. Commit or stash them first.")


class Journal:
    """What is in progress and what has been applied, in ``journal.json`` next to the work."""

    def __init__(self, state: Path) -> None:
        self.path = state / "journal.json"

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = {}
        data.setdefault("proposal", None)
        data.setdefault("applied", [])
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps(data, indent=2))
        os.replace(temp, self.path)


_RAN = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)
_SKIPPED = re.compile(r"skipped=(\d+)")


def test_command() -> list[str]:
    """The test run for a checkout. Tests that open windows run in a virtual display if there is one; otherwise
    there is no display at all, so they skip rather than flashing windows on the user's desktop."""
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."]
    virtual = shutil.which("xvfb-run")
    return [virtual, "-a", *command] if virtual else command


def run_tests(path: Path, command: Sequence[str] | None = None, timeout: float = TEST_TIMEOUT_S) -> tuple[bool, str]:
    """Run the test suite in ``path``. (passed, a short summary for the model)."""
    cmd = list(command) if command else test_command()
    env = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(path)}
    if not command and not shutil.which("xvfb-run"):
        env.update(DISPLAY="", WAYLAND_DISPLAY="")  # no display at all: the tests that open windows skip themselves
    code, output = run(cmd, path, timeout, env)
    ran = _RAN.search(output)
    skipped = _SKIPPED.search(output)
    counts = f"{ran.group(1)} tests" + (f", {skipped.group(1)} skipped" if skipped else "") if ran else "no test results"
    if code == 0:
        return True, f"passed ({counts})"
    tail = "\n".join(output.splitlines()[-25:])
    return False, f"FAILED ({counts}). The end of the output:\n{tail}"

