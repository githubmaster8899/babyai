"""Changing his own code - on a branch, with the user approving what goes live.

The workflow: ``start`` makes a git branch with its own working folder (a git worktree outside the checkout he is
running from), ``write`` / ``replace`` edit files in it, ``test`` runs the test suite there, ``diff`` shows what
changed, and ``apply`` fast-forwards the live checkout to the branch - only once the tests have passed on exactly
that version, and only if the user says yes (the safety gate asks; see babyai/safety). ``discard`` throws the
branch away. ``VersionUpdates.rollback`` undoes the last thing that was applied.

Guard rails, which hold whatever the model asks for:

* edits can only land inside the branch's folder, never through a symlink, and never in PROTECTED paths - the
  safety rules, this package, the tools that drive it, the place the gate is enforced, and the licence files;
* a change goes live only through ``apply``, which needs the tests to have passed on the same commit;
* nothing here pushes anywhere or touches any other branch.

These are guard rails for this workflow, not a sandbox: with the user's approval he still has ``run_shell``.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Sequence

from babyai.upgrade.gitops import (
    IDENTITY,
    Journal,
    UpgradeError,
    git,
    require_clean,
    run_tests,
    short,
)

PROTECTED = (
    "babyai/safety/",  # the rules and the gate
    "babyai/upgrade/",  # this workflow
    "babyai/tools/upgrade.py",  # the tools that drive it
    "babyai/agent/core.py",  # where the gate is enforced
    "tests/test_safety.py",
    "tests/test_upgrade.py",
    "NOTICE",
    "LICENSE",
    "capri/LICENSE.md",  # Capri's licence notices must stay intact
    ".git/",
)
MAX_FILE_BYTES = 200_000
MAX_FILES = 40
DIFF_TO_MODEL = 6000  # characters of the diff handed back; the whole of it is saved to a file


def is_protected(relative: str) -> bool:
    lowered = relative.lower().strip("/")
    parts = lowered.split("/")
    if ".git" in parts:
        return True
    return any(lowered == rule.rstrip("/") or lowered.startswith(rule) for rule in (p.lower() for p in PROTECTED))


class CodeUpgrades:
    def __init__(self, repo: Path | None, state: Path, test_command: Sequence[str] | None = None) -> None:
        self.repo = repo
        self.state = state
        self.journal = Journal(state)
        self.test_command = list(test_command) if test_command else None  # tests override this; None = the real suite

    # ------------------------------------------------------------------ helpers

    def _repo(self) -> Path:
        if self.repo is None or not (self.repo / ".git").exists():
            raise UpgradeError("I'm not running from a git checkout, so I can't change or update my own code.")
        return self.repo

    def _proposal(self) -> dict[str, Any]:
        proposal = self.journal.load()["proposal"]
        if not proposal:
            raise UpgradeError("There's no change in progress. Start one with action=start and a goal.")
        if not Path(proposal["path"]).is_dir():
            self._forget()
            raise UpgradeError("That change's working folder is gone, so I've dropped it. Start again.")
        return proposal

    def _save_proposal(self, proposal: dict[str, Any] | None) -> None:
        data = self.journal.load()
        data["proposal"] = proposal
        self.journal.save(data)

    def _forget(self) -> None:
        self._save_proposal(None)

    def _snapshot(self, proposal: dict[str, Any]) -> str:
        """Commit whatever has been written so far on the branch; the commit is the version the tests vouch for."""
        folder = Path(proposal["path"])
        if git(folder, "status", "--porcelain"):
            git(folder, "add", "-A")
            git(folder, *IDENTITY, "commit", "-q", "-m", f"self-upgrade: {proposal['goal']}")
        return git(folder, "rev-parse", "HEAD")

    def _target(self, proposal: dict[str, Any], relative: str) -> Path:
        folder = Path(proposal["path"])
        text = (relative or "").strip().replace("\\", "/")
        if not text or text.startswith("/") or ".." in Path(text).parts:
            raise UpgradeError("Give a path inside the repo, like babyai/tools/clock.py.")
        target = folder / text
        real, root = Path(os.path.realpath(target)), Path(os.path.realpath(folder))
        if real != root and root not in real.parents:
            raise UpgradeError("That path leads outside my working folder, so I won't touch it.")
        for candidate in (text, real.relative_to(root).as_posix()):
            if is_protected(candidate):
                raise UpgradeError(
                    f"{candidate} is protected (the safety rules, the upgrade tools and the licence files can only be changed by you, by hand)."
                )
        return target

    def _changed(self, folder: Path, base: str) -> list[str]:
        return [line for line in git(folder, "diff", "--name-only", base).splitlines() if line] + [
            line for line in git(folder, "ls-files", "--others", "--exclude-standard").splitlines() if line
        ]

    # ------------------------------------------------------------------ the workflow

    def start(self, goal: str) -> str:
        repo = self._repo()
        if self.journal.load()["proposal"]:
            raise UpgradeError("A change is already in progress. Finish it (test, diff, apply) or throw it away with action=discard first.")
        goal = " ".join((goal or "").split())[:120]
        if not goal:
            raise UpgradeError("Say what the change is for, e.g. goal='add a tool that tells the time'.")
        base = git(repo, "rev-parse", "HEAD")
        slug = re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")[:30] or "change"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        branch, folder = f"self/{slug}-{stamp}", self.state / "worktrees" / f"{slug}-{stamp}"
        folder.parent.mkdir(parents=True, exist_ok=True)
        git(repo, "worktree", "add", "-q", "-b", branch, str(folder), base)
        self._save_proposal({"goal": goal, "branch": branch, "path": str(folder), "base": base, "tests": None})
        return (
            f"Started a change on branch {branch}: {goal}. Work in {folder}. "
            "Use action=write (path, content) or action=replace (path, old, new), then test, diff and apply."
        )

    def write(self, path: str, content: str) -> str:
        proposal = self._proposal()
        target = self._target(proposal, path)
        data = (content or "").encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise UpgradeError(f"That file is bigger than {MAX_FILE_BYTES // 1000} KB; split it up.")
        if not target.exists() and len(self._changed(Path(proposal["path"]), proposal["base"])) >= MAX_FILES:
            raise UpgradeError(f"A change can touch at most {MAX_FILES} files; finish this one first.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return f"Wrote {path} ({len(data)} bytes) on {proposal['branch']}."

    def replace(self, path: str, old: str, new: str) -> str:
        proposal = self._proposal()
        target = self._target(proposal, path)
        if not target.is_file():
            raise UpgradeError(f"{path} doesn't exist on this branch.")
        text = target.read_text(encoding="utf-8")
        found = text.count(old) if old else 0
        if found != 1:
            raise UpgradeError(f"The text to replace must appear exactly once in {path}; it appears {found} times.")
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"Edited {path} on {proposal['branch']}."

    def _stat(self, proposal: dict[str, Any], tip: str) -> tuple[str, list[str]]:
        folder = Path(proposal["path"])
        stat = git(folder, "diff", "--shortstat", f"{proposal['base']}..{tip}") or "no changes"
        names = git(folder, "diff", "--name-only", f"{proposal['base']}..{tip}").splitlines()
        return stat.strip(), names

    def diff(self) -> str:
        proposal = self._proposal()
        tip = self._snapshot(proposal)
        if tip == proposal["base"]:
            return f"Nothing has changed on {proposal['branch']} yet."
        stat, names = self._stat(proposal, tip)
        full = git(Path(proposal["path"]), "diff", f"{proposal['base']}..{tip}")
        saved = self.state / "proposals" / f"{Path(proposal['path']).name}.diff"
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text(full)
        shown = full if len(full) <= DIFF_TO_MODEL else full[:DIFF_TO_MODEL] + "\n... (cut; the whole diff is in the file below)"
        tests = proposal.get("tests") or {}
        verdict = "tests passed on this version" if tests.get("ok") and tests.get("tip") == tip else "tests have NOT passed on this version yet"
        return (
            f"{proposal['branch']}: {stat} ({', '.join(names)}); {verdict}.\n"
            f"The whole diff is saved at {saved}; the user can also read it with: git -C {self._repo()} diff {short(proposal['base'])}..{proposal['branch']}\n\n{shown}"
        )

    def test(self) -> str:
        proposal = self._proposal()
        tip = self._snapshot(proposal)
        if tip == proposal["base"]:
            raise UpgradeError("Nothing has changed on this branch yet, so there is nothing to test.")
        ok, summary = run_tests(Path(proposal["path"]), self.test_command)
        data = self.journal.load()
        data["proposal"]["tests"] = {"tip": tip, "ok": ok, "summary": summary.splitlines()[0]}
        self.journal.save(data)
        return f"Tests {summary}" + ("\nNow show the user the diff, and apply it if they agree." if ok else "")

    def preview_apply(self) -> dict[str, str]:
        """What the user is shown when asked whether to make the change live."""
        proposal = self._proposal()
        tip = self._snapshot(proposal)
        stat, names = self._stat(proposal, tip)
        tests = proposal.get("tests") or {}
        passed = bool(tests.get("ok") and tests.get("tip") == tip)
        shown = {"change": proposal["goal"], "branch": proposal["branch"], "diff": stat, "files": ", ".join(names)[:140] or "none"}
        shown["tests"] = "passed on this version" if passed else "NOT passed on this version"
        shown["protected"] = "untouched"
        return shown

    def preview_test(self) -> dict[str, str]:
        proposal = self._proposal()
        return {"run the tests on": proposal["branch"], "this runs": "the code I wrote on that branch"}

    def apply(self) -> str:
        proposal = self._proposal()
        repo = self._repo()
        tip = self._snapshot(proposal)
        if tip == proposal["base"]:
            raise UpgradeError("Nothing has changed on this branch, so there is nothing to apply.")
        tests = proposal.get("tests") or {}
        if not (tests.get("ok") and tests.get("tip") == tip):
            raise UpgradeError("The tests haven't passed on the latest version of this change. Run action=test first (and again after any edit).")
        for name in self._changed(Path(proposal["path"]), proposal["base"]):
            if is_protected(name):  # cannot happen through write/replace; checked again because this is the step that matters
                raise UpgradeError(f"The change touches {name}, which only the user may change by hand. I won't apply it.")
        require_clean(repo)
        head = git(repo, "rev-parse", "HEAD")
        if head != proposal["base"]:
            raise UpgradeError("My own folder has moved on since this change was started, so it no longer applies cleanly. Discard it and start again.")
        git(repo, "merge", "--ff-only", "-q", proposal["branch"])
        data = self.journal.load()
        data["applied"].append({
            "kind": "code", "goal": proposal["goal"], "branch": proposal["branch"], "before": head, "after": tip,
            "when": time.strftime("%Y-%m-%d %H:%M:%S"), "rolled_back": False,
        })
        data["proposal"] = None
        self.journal.save(data)
        git(repo, "worktree", "remove", "--force", proposal["path"], check=False)  # the branch stays, for the record
        return (
            f"Applied: {proposal['goal']} ({short(head)} -> {short(tip)}), tests passed. It is live in the files, "
            "but I only pick it up after a restart (action=restart on upgrade_version). To undo it: upgrade_version rollback."
        )

    def discard(self) -> str:
        proposal = self._proposal()
        repo = self._repo()
        git(repo, "worktree", "remove", "--force", proposal["path"], check=False)
        shutil.rmtree(proposal["path"], ignore_errors=True)
        git(repo, "worktree", "prune", check=False)
        git(repo, "branch", "-D", proposal["branch"], check=False)
        self._forget()
        return f"Threw away {proposal['branch']}."

    def status(self) -> str:
        data = self.journal.load()
        proposal = data["proposal"]
        lines = []
        if proposal and Path(proposal["path"]).is_dir():
            tip = self._snapshot(proposal)
            stat, names = self._stat(proposal, tip)
            tests = proposal.get("tests")
            lines.append(
                f"In progress: {proposal['goal']} on {proposal['branch']} ({stat}"
                + (f": {', '.join(names)}" if names else "") + "); "
                + ("tests " + tests["summary"] + ("" if tests["tip"] == tip else " (before later edits)") if tests else "not tested yet")
            )
        else:
            lines.append("No change in progress.")
        for entry in data["applied"][-3:]:
            lines.append(f"Applied {entry['when']}: {entry['goal']}" + (" (rolled back)" if entry["rolled_back"] else ""))
        return "\n".join(lines)
