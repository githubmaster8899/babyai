"""Changing his own code - on a branch, with the user approving what goes live.

The workflow: ``start`` makes a git branch with its own working folder (a git worktree outside the checkout he is
running from), ``write`` / ``replace`` edit files in it, ``test`` runs the test suite there, ``diff`` shows what
changed, and ``apply`` fast-forwards the live checkout to the branch - only once the tests have passed on exactly
that version, and only if the user says yes (the safety gate asks; see babyai/safety). ``discard`` throws the
branch away. ``VersionUpdates.rollback`` undoes the last thing that was applied.

Guard rails, which hold whatever the model asks for:

* edits can only land inside the branch's folder, never through a symlink, and never in PROTECTED paths - the
  safety rules and gate, the place the gate is enforced, the licence files, and the test file below;
* a change goes live only through ``apply``, which needs the tests to have passed on the same commit;
* nothing here pushes anywhere or touches any other branch.

He may change this package too (UPGRADE_CODE). Then what holds him back can't live only in code he can edit, so
``tests/test_guardrails.py`` is protected and pins these rules: a change that weakens one of them fails that file,
and a change whose tests fail is never applied. The change is also flagged in what the user is asked, and takes effect
only after a restart - the code that checks and applies it is still the version he is running, not the new one.

These are guard rails against mistakes and sloppy edits, not a sandbox: with the user's approval he still has
``run_shell``, and the user reading the diff before saying yes is the real control.
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
    "babyai/agent/core.py",  # where the gate is enforced
    "tests/test_safety.py",
    "tests/test_guardrails.py",  # pins how the upgrade code below must behave
    "NOTICE",
    "LICENSE",
    "capri/LICENSE.md",  # Capri's licence notices must stay intact
    ".git/",
)
UPGRADE_CODE = (  # his own upgrade machinery: he may change it, and the user is told when he does
    "babyai/upgrade/",
    "babyai/tools/upgrade.py",
    "tests/test_upgrade.py",
    "tests/test_ideas.py",
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


def edits_upgrade_code(names: Sequence[str]) -> list[str]:
    """Those of ``names`` that are part of his own upgrade machinery."""
    rules = [rule.lower() for rule in UPGRADE_CODE]
    return [name for name in names if any(name.lower().strip("/") == rule.rstrip("/") or name.lower().startswith(rule) for rule in rules)]


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
                    f"{candidate} is protected (the safety rules and gate, the guard-rail tests and the licence files can only be changed by you, by hand)."
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

    def _changed_between(self, repo: Path, before: str, after: str) -> list[str]:
        return git(repo, "diff", "--name-only", f"{before}..{after}").splitlines()

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
        own = edits_upgrade_code(names)
        flag = f" NOTE: this edits my own upgrade code ({', '.join(own)}) - read this diff with extra care; it takes effect after a restart." if own else ""
        return (
            f"{proposal['branch']}: {stat} ({', '.join(names)}); {verdict}.{flag}\n"
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
        shown["upgrade code"] = "CHANGED (live after restart)" if edits_upgrade_code(names) else "untouched"
        return shown

    def preview_test(self) -> dict[str, str]:
        proposal = self._proposal()
        shown = {"run the tests on": proposal["branch"], "this runs": "the code I wrote on that branch"}
        own = edits_upgrade_code(self._stat(proposal, self._snapshot(proposal))[1])
        if own:
            shown["also changes"] = "my own upgrade code"
        return shown

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
        own = edits_upgrade_code(self._changed_between(repo, head, tip))
        note = " It changes my own upgrade code, so I'll be working differently after the restart." if own else ""
        return (
            f"Applied: {proposal['goal']} ({short(head)} -> {short(tip)}), tests passed. It is live in the files, "
            f"but I only pick it up after a restart (action=restart on upgrade_version).{note} "
            f"To undo it: upgrade_version rollback, or by hand: git -C {repo} reset --hard {short(head)}"
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
