"""A few words for what he is doing while he works, for the overlay to show next to "THINKING".

Only the *kind* of thing is named ("searching the web"), from a fixed list. What the model typed - a file name, a
search, a command - is never part of it, so the line can't leak anything and can't be made to say anything."""

from __future__ import annotations

import re

LABELS = {
    "read_file": "reading a file",
    "write_file": "writing a file",
    "edit_file": "editing a file",
    "list_directory": "looking in a folder",
    "find_files": "finding files",
    "search_files": "searching files",
    "run_shell": "running a command",
    "web_search": "searching the web",
    "fetch_url": "reading a web page",
    "get_system_info": "checking the system",
    "manage_processes": "checking what is running",
    "remember_fact": "remembering that",
    "recall_memory": "thinking back",
    "forget_memory": "forgetting something",
    "get_clipboard": "reading the clipboard",
    "set_clipboard": "copying to the clipboard",
    "notify": "sending a notice",
    "speak": "speaking",
    "listen": "listening",
    "capture_screen": "taking a screenshot",
    "look_at_screen": "looking at the screen",
    "get_active_window": "checking the window in front",
    "ask_user": "asking you",
    "personality": "checking how she sounds",
}

_NAME = re.compile(r"[a-z][a-z0-9_]{0,40}")


def describe_tool(name: str) -> str:
    """A short phrase for the tool, or "using <name>" for one that has no phrase yet. Empty for a name that is not a tool name."""
    label = LABELS.get(name)
    if label:
        return label
    if isinstance(name, str) and _NAME.fullmatch(name):
        return "using " + name.replace("_", " ")
    return ""
