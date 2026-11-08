"""Letting him upgrade himself: change his own code on a branch, update from GitHub, switch to a better model,
and teach himself notes and skills - every change approved by the user first. See babyai/tools/upgrade.py for the
tools the model uses, and babyai/safety for the part that always asks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import babyai
from babyai.upgrade.code import CodeUpgrades
from babyai.upgrade.models import ModelUpgrades
from babyai.upgrade.notes import NoteBook
from babyai.upgrade.update import VersionUpdates


class UpgradeContext:
    """Everything the four upgrade tools share, set up from the ``[upgrade]`` section of the config."""

    def __init__(self, assistant: Any, config: dict[str, Any], config_path: str | None = None, repo: Path | None | str = "auto") -> None:
        settings = config.get("upgrade", {})
        self.state = Path(str(settings.get("dir") or "~/.local/share/babyai/self")).expanduser()
        if repo == "auto":  # the checkout this code is running from, if it is one
            guess = Path(babyai.__file__).resolve().parents[1]
            repo = guess if (guess / ".git").exists() else None
        self.repo = Path(repo) if repo else None
        self.code = CodeUpgrades(self.repo, self.state)
        self.version = VersionUpdates(self.repo, self.state)
        self.models = ModelUpgrades(assistant, config_path)
        self.notes = NoteBook(self.state)
