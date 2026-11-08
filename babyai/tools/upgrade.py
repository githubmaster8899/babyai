"""The tools he upgrades himself with. Each takes an ``action``; the safety gate rates every action on its own
(babyai/safety/rules.py: UPGRADE_ACTION_RISK) and always asks the user before one that changes him or his machine.
The work is in babyai/upgrade."""

from __future__ import annotations

from typing import Any, Callable

from babyai.tools.base import Tool, ToolResult
from babyai.upgrade.gitops import UpgradeError


class _UpgradeTool(Tool):
    def __init__(self, memory_store: Any = None, config: dict[str, Any] | None = None, upgrade: Any = None):
        super().__init__(memory_store, config)
        self.upgrade = upgrade

    def _dispatch(self, action: str, handlers: dict[str, Callable[[], str]]) -> ToolResult:
        name = (action or "").strip().lower()
        handler = handlers.get(name)
        if handler is None:
            return ToolResult(False, f"{action!r} isn't an action of {self.name}. Actions: {', '.join(handlers)}.")
        try:
            return ToolResult(True, handler())
        except UpgradeError as exc:
            return ToolResult(False, str(exc))


class UpgradeCodeTool(_UpgradeTool):
    name = "upgrade_code"
    description = (
        "Change your own code, safely, on a separate git branch. The user approves the test run and the final apply. "
        "Actions: start (goal) - begin a change; write (path, content) - write a whole file on the branch; "
        "replace (path, old, new) - edit text in a file on the branch; test - run the test suite on the branch; "
        "diff - show what changed; apply - make it live (only after the tests passed); discard - throw the branch away; "
        "status. Order: start, write/replace, test, diff, apply. The safety rules, these tools and the licence files can't be edited."
    )

    def invoke(self, action: str, goal: str = "", path: str = "", content: str = "", old: str = "", new: str = "") -> ToolResult:
        code = self.upgrade.code
        return self._dispatch(action, {
            "status": code.status, "start": lambda: code.start(goal), "write": lambda: code.write(path, content),
            "replace": lambda: code.replace(path, old, new), "diff": code.diff, "test": code.test, "apply": code.apply,
            "discard": code.discard,
        })

    def preview(self, action: str = "", **_: Any) -> dict[str, Any] | None:
        code = self.upgrade.code
        try:
            if (action or "").strip().lower() == "apply":
                return code.preview_apply()
            if (action or "").strip().lower() == "test":
                return code.preview_test()
        except UpgradeError:
            return None
        return None


class UpgradeVersionTool(_UpgradeTool):
    name = "upgrade_version"
    description = (
        "Update yourself from GitHub, undo the last change, or restart. Actions: check - see whether newer commits exist; "
        "update - pull them (their tests run first, the user approves); rollback - undo the last applied change or update; "
        "restart - restart yourself to pick up changes (the conversation is lost, memory is kept)."
    )

    def invoke(self, action: str) -> ToolResult:
        version = self.upgrade.version
        return self._dispatch(action, {
            "check": version.check, "update": version.update, "rollback": version.rollback, "restart": version.restart,
        })

    def preview(self, action: str = "", **_: Any) -> dict[str, Any] | None:
        version = self.upgrade.version
        name = (action or "").strip().lower()
        try:
            if name == "update":
                return version.preview_update()
            if name == "rollback":
                return version.preview_rollback()
        except UpgradeError:
            return None
        if name == "restart":
            return {"restart": "close me and open me again now", "lost": "this conversation (memory is kept)"}
        return None


class UpgradeModelTool(_UpgradeTool):
    name = "upgrade_model"
    description = (
        "Upgrade your brain: list the models the server has, pull a new one, or switch to one. Actions: list; "
        "pull (model) - download a model, e.g. llama3.1:8b; switch (model) - try it and use it from now on. "
        "The user approves pull and switch. A model that can't use tools is refused."
    )

    def invoke(self, action: str, model: str = "") -> ToolResult:
        models = self.upgrade.models
        return self._dispatch(action, {"list": models.list, "pull": lambda: models.pull(model), "switch": lambda: models.switch(model)})

    def preview(self, action: str = "", model: str = "", **_: Any) -> dict[str, Any] | None:
        models = self.upgrade.models
        try:
            if (action or "").strip().lower() == "pull":
                return models.preview_pull(model)
            if (action or "").strip().lower() == "switch":
                return models.preview_switch(model)
        except UpgradeError:
            return None
        return None


class UpgradeNotesTool(_UpgradeTool):
    name = "upgrade_notes"
    description = (
        "Teach yourself: keep short notes about how to be (they join your instructions) and write skills (named how-tos you can read back). "
        "Actions: show - your notes and skill names; add_note (text); remove_note (index); save_skill (name, text); "
        "show_skill (name); delete_skill (name). The user approves every change. Notes never override your rules."
    )

    def invoke(self, action: str, text: str = "", name: str = "", index: int = 0) -> ToolResult:
        book = self.upgrade.notes

        def show() -> str:
            notes = book.notes()
            lines = [f"{i}. {note}" for i, note in enumerate(notes, 1)] or ["(no notes)"]
            return "Notes:\n" + "\n".join(lines) + "\nSkills: " + (", ".join(book.skills()) or "none")

        return self._dispatch(action, {
            "show": show, "add_note": lambda: book.add_note(text), "remove_note": lambda: book.remove_note(int(index)),
            "save_skill": lambda: book.save_skill(name, text), "show_skill": lambda: book.show_skill(name),
            "delete_skill": lambda: book.delete_skill(name),
        })

    def preview(self, action: str = "", text: str = "", name: str = "", index: int = 0, **_: Any) -> dict[str, Any] | None:
        book = self.upgrade.notes
        kind = (action or "").strip().lower()
        try:
            if kind == "add_note":
                return {"add this note": book.check_note(text)}
            if kind == "remove_note":
                return {"remove note": f"#{index}: {book.note_at(int(index)) or '(none)'}"}
            if kind == "save_skill":
                return {"save the skill": name, "text": " ".join((text or "").split())[:140]}
            if kind == "delete_skill":
                return {"delete the skill": name}
        except (UpgradeError, ValueError):
            return None
        return None
