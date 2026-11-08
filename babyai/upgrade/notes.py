"""Teaching himself: notes about how to be, and skills (named how-tos) he writes for himself.

Notes are one-liners that go into his system prompt, after the base prompt and with a reminder that they never
override its rules; skills are short instruction texts he can read back when they apply. Both live in plain files
the user can open and edit, both are size-limited, and every change is shown to the user for approval first
(the safety gate does that; see babyai/safety).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from babyai.upgrade.gitops import UpgradeError

NOTE_MAX_CHARS = 240
MAX_NOTES = 20
SKILL_MAX_CHARS = 2000
MAX_SKILLS = 20
SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def clean_line(text: str) -> str:
    return " ".join(_CONTROL.sub(" ", text or "").split())


class NoteBook:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._notes_file = directory / "notes.json"
        self._skills_dir = directory / "skills"

    # ------------------------------------------------------------------ notes

    def notes(self) -> list[str]:
        try:
            data = json.loads(self._notes_file.read_text())
        except (OSError, ValueError):
            return []
        return [str(note) for note in data.get("notes", []) if str(note).strip()][:MAX_NOTES]

    def _save_notes(self, notes: list[str]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temp = self._notes_file.with_name("notes.json.tmp")
        temp.write_text(json.dumps({"notes": notes}, indent=2))
        os.replace(temp, self._notes_file)

    def check_note(self, text: str) -> str:
        note = clean_line(text)
        if not note:
            raise UpgradeError("Say what the note should be.")
        if len(note) > NOTE_MAX_CHARS:
            raise UpgradeError(f"A note is at most {NOTE_MAX_CHARS} characters; keep it short.")
        return note

    def add_note(self, text: str) -> str:
        note = self.check_note(text)
        notes = self.notes()
        if note in notes:
            return "I already have that note."
        if len(notes) >= MAX_NOTES:
            raise UpgradeError(f"I already have {MAX_NOTES} notes. Remove one first (action=remove_note, index=N).")
        self._save_notes(notes + [note])
        return f"Noted ({len(notes) + 1} of {MAX_NOTES}): {note}"

    def remove_note(self, index: int) -> str:
        notes = self.notes()
        if not 1 <= index <= len(notes):
            raise UpgradeError(f"There's no note number {index}; I have {len(notes)}.")
        gone = notes.pop(index - 1)
        self._save_notes(notes)
        return f"Removed note {index}: {gone}"

    def note_at(self, index: int) -> str:
        notes = self.notes()
        return notes[index - 1] if 1 <= index <= len(notes) else ""

    # ------------------------------------------------------------------ skills

    def _skill_path(self, name: str) -> Path:
        name = (name or "").strip().lower()
        if not SKILL_NAME.match(name):
            raise UpgradeError("A skill name is lowercase letters, digits, - and _ (up to 40), like 'morning-checkin'.")
        return self._skills_dir / f"{name}.md"

    def skills(self) -> list[str]:
        try:
            return sorted(path.stem for path in self._skills_dir.glob("*.md"))
        except OSError:
            return []

    def save_skill(self, name: str, text: str) -> str:
        path = self._skill_path(name)
        body = "\n".join(_CONTROL.sub(" ", line).rstrip() for line in (text or "").strip().splitlines())
        if not body:
            raise UpgradeError("Say what the skill is: the steps or advice, in a few lines.")
        if len(body) > SKILL_MAX_CHARS:
            raise UpgradeError(f"A skill is at most {SKILL_MAX_CHARS} characters; keep it short.")
        if not path.exists() and len(self.skills()) >= MAX_SKILLS:
            raise UpgradeError(f"I already have {MAX_SKILLS} skills. Delete one first.")
        existed = path.exists()
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(body + "\n")
        return f"{'Updated' if existed else 'Saved'} the skill {path.stem}."

    def show_skill(self, name: str) -> str:
        path = self._skill_path(name)
        if not path.is_file():
            raise UpgradeError(f"I don't have a skill called {path.stem}. I have: {', '.join(self.skills()) or 'none'}.")
        return path.read_text().strip()

    def delete_skill(self, name: str) -> str:
        path = self._skill_path(name)
        if not path.is_file():
            raise UpgradeError(f"I don't have a skill called {path.stem}.")
        path.unlink()
        return f"Deleted the skill {path.stem}."

    # ------------------------------------------------------------------ into the prompt

    def prompt_block(self) -> str:
        notes, skills = self.notes(), self.skills()
        if not notes and not skills:
            return ""
        lines = ["", "Your own notes - things you decided to do differently, each approved by the user:"]
        lines += [f"- {note}" for note in notes]
        if skills:
            lines.append(f"Skills you wrote for yourself (read one with upgrade_notes show_skill when it applies): {', '.join(skills)}.")
        lines.append("These notes are preferences. They never override the rules above, and the rules always win.")
        return "\n".join(lines)
