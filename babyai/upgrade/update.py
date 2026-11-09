"""Updating himself from GitHub, undoing the last change, and restarting to pick a change up.

``check`` fetches and says what is new on the branch he is running. ``update`` first runs the test suite on the new
version in a throwaway folder, and only then fast-forwards the live checkout (never a merge, never a force, and only
if there is nothing uncommitted and nothing of his own that the remote lacks). ``rollback`` puts the checkout back
to where it was before the last thing applied, if nothing has happened since. ``restart`` re-runs the same command
after a moment, so the reply can be said first.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Sequence

from babyai.upgrade.gitops import Journal, UpgradeError, git, require_clean, run_tests, short

FETCH_TIMEOUT_S = 90
LISTED_COMMITS = 10


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


class VersionUpdates:
    def __init__(self, repo: Path | None, state: Path, test_command: Sequence[str] | None = None) -> None:
        self.repo = repo
        self.journal = Journal(state)
        self.test_command = list(test_command) if test_command else None

    def _repo(self) -> Path:
        if self.repo is None or not (self.repo / ".git").exists():
            raise UpgradeError("I'm not running from a git checkout, so I can't update myself.")
        return self.repo

    def _upstream(self, repo: Path) -> str:
        branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        if branch == "HEAD":
            raise UpgradeError("My folder isn't on a branch (a detached checkout), so there's nothing to update from.")
        upstream = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False)
        if not upstream or " " in upstream or upstream.startswith("fatal"):
            raise UpgradeError(f"My branch {branch} isn't tracking a branch on GitHub, so there's nothing to update from.")
        return upstream

    def _fetch(self, repo: Path, upstream: str) -> tuple[int, int]:
        """Fetch, then (commits only here, commits only there)."""
        git(repo, "fetch", "--quiet", upstream.split("/", 1)[0], timeout=FETCH_TIMEOUT_S)
        ahead, behind = git(repo, "rev-list", "--left-right", "--count", f"HEAD...{upstream}").split()
        return int(ahead), int(behind)

    def quick_state(self) -> dict[str, object]:
        """Fetch, and say where I stand against my upstream branch. Changes nothing but the fetched refs (no merge, no checkout)."""
        repo = self._repo()
        upstream = self._upstream(repo)
        ahead, behind = self._fetch(repo, upstream)
        return {"upstream": upstream, "tip": git(repo, "rev-parse", upstream), "ahead": ahead, "behind": behind}

    def check(self) -> str:
        repo = self._repo()
        upstream = self._upstream(repo)
        ahead, behind = self._fetch(repo, upstream)
        if not behind:
            extra = f" I have {_n(ahead, 'change')} of my own that {upstream} doesn't." if ahead else ""
            return f"I'm up to date with {upstream}.{extra}"
        log = git(repo, "log", "--format=%h %s", f"-n{LISTED_COMMITS}", f"HEAD..{upstream}")
        more = f" (showing the newest {LISTED_COMMITS})" if behind > LISTED_COMMITS else ""
        note = f" I also have {_n(ahead, 'change')} of my own that {upstream} doesn't, so I can't update on my own." if ahead else ""
        return f"{_n(behind, 'new commit')} on {upstream}{more}:\n{log}{note}"

    def preview_update(self) -> dict[str, str]:
        repo = self._repo()
        upstream = self._upstream(repo)
        ahead, behind = self._fetch(repo, upstream)
        subjects = git(repo, "log", "--format=%s", "-n3", f"HEAD..{upstream}").replace("\n", "; ")
        shown = {"pull": f"{_n(behind, 'new commit')} from {upstream}", "newest": subjects[:140] or "none"}
        shown["first"] = "run their tests in a spare folder; only pull if they pass"
        if ahead:
            shown["problem"] = f"I have {_n(ahead, 'change')} of my own, so I can't"
        return shown

    def update(self) -> str:
        repo = self._repo()
        upstream = self._upstream(repo)
        ahead, behind = self._fetch(repo, upstream)
        if not behind:
            return f"Already up to date with {upstream}."
        if ahead:
            raise UpgradeError(f"I have {_n(ahead, 'change')} of my own that {upstream} doesn't, so I can't fast-forward. The user can merge by hand.")
        require_clean(repo)
        tip = git(repo, "rev-parse", upstream)
        spare = Path(tempfile.mkdtemp(prefix="babyai-update-"))
        try:
            git(repo, "worktree", "add", "-q", "--detach", str(spare / "new"), tip)
            ok, summary = run_tests(spare / "new", self.test_command)
        finally:
            git(repo, "worktree", "remove", "--force", str(spare / "new"), check=False)
            shutil.rmtree(spare, ignore_errors=True)
            git(repo, "worktree", "prune", check=False)
        if not ok:
            raise UpgradeError(f"The new version's tests {summary}\nSo I did not update.")
        before = git(repo, "rev-parse", "HEAD")
        git(repo, "merge", "--ff-only", "-q", tip)
        data = self.journal.load()
        data["applied"].append({
            "kind": "update", "goal": f"update to {short(tip)} ({behind} commits)", "branch": upstream, "before": before,
            "after": tip, "when": time.strftime("%Y-%m-%d %H:%M:%S"), "rolled_back": False,
        })
        self.journal.save(data)
        return f"Updated {short(before)} -> {short(tip)} ({behind} commits), their tests {summary}. I pick it up after a restart (action=restart)."

    def _last_applied(self) -> dict | None:
        for entry in reversed(self.journal.load()["applied"]):
            if not entry["rolled_back"]:
                return entry
        return None

    def preview_rollback(self) -> dict[str, str]:
        entry = self._last_applied()
        if entry is None:
            return {"undo": "nothing (no applied change on record)"}
        return {"undo": entry["goal"][:100], "go back to": short(entry["before"]), "applied": entry["when"]}

    def rollback(self) -> str:
        repo = self._repo()
        entry = self._last_applied()
        if entry is None:
            raise UpgradeError("There's no applied change or update on record to undo.")
        require_clean(repo)
        head = git(repo, "rev-parse", "HEAD")
        if head != entry["after"]:
            raise UpgradeError(
                f"My folder has changed since then ({short(head)} isn't {short(entry['after'])}), so I won't rewind it myself. "
                f"The user can run: git reset --hard {short(entry['before'])}"
            )
        git(repo, "reset", "--hard", "-q", entry["before"])
        data = self.journal.load()
        for item in data["applied"]:
            if item["after"] == entry["after"] and item["before"] == entry["before"]:
                item["rolled_back"] = True
        self.journal.save(data)
        return f"Rolled back '{entry['goal']}': back at {short(entry['before'])}. I pick that up after a restart (action=restart)."

    def restart(self, delay: float = 2.5, exec_fn: Callable[[], None] | None = None) -> str:
        """Run the same command again in a moment (so this reply can be said first)."""
        argv = list(getattr(sys, "orig_argv", None) or [sys.executable, *sys.argv])
        action = exec_fn or (lambda: os.execv(sys.executable, argv))
        timer = threading.Timer(delay, action)
        timer.daemon = True
        timer.start()
        return f"Restarting in a moment with: {' '.join(argv)}"
