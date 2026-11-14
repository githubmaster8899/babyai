"""Answering a question about what is on the screen: one screenshot, one vision model, then the file is gone.

A separate tool from ``capture_screen`` on purpose. ``capture_screen`` saves a picture where the user asks;
this one shows it to a model and deletes it again, so its answer can mention what was on screen but nothing
is left behind. Both are CRITICAL in the safety rules, so the user is always asked first.

The vision model is opt-in: set ``[llm] vision_model`` (for example ``llava`` or ``qwen2.5vl``) in config.toml.
With no vision model the tool says exactly that instead of sending a picture to a text model that can't see.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any
from urllib.parse import urlparse

from babyai import screen
from babyai.llm.client import LLMClient
from babyai.tools.base import Tool, ToolResult

DEFAULT_QUESTION = "What is on my screen? Describe the parts that matter, briefly."
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class LookAtScreenTool(Tool):
    name = "look_at_screen"
    description = (
        "Take one screenshot and answer a question about it. Use it only when the user asks what is on their screen or "
        "wants help with something they are looking at. The picture is sent to the vision model on their own machine, if "
        "one is configured, and deleted right after; nothing is kept. If it says no vision model is configured, tell them "
        "they can set [llm] vision_model in config.toml."
    )

    def __init__(self, memory_store: Any = None, config: dict[str, Any] | None = None, client: Any = None):
        super().__init__(memory_store, config)
        self.client = client  # tests pass a stand-in; normally built from config for each look

    def invoke(self, question: str = DEFAULT_QUESTION) -> ToolResult:
        llm = self.config.get("llm", {}) if isinstance(self.config, dict) else {}
        model = str(llm.get("vision_model") or "").strip()
        if not model and self.client is None:
            return ToolResult(
                False,
                "I can look at your screen, but I need a vision model first. Set [llm] vision_model in config.toml "
                "(for example llava or qwen2.5vl) and pull it with `ollama pull`.",
            )
        with tempfile.TemporaryDirectory(prefix="babyai-screen-") as folder:
            path = os.path.join(folder, "screen.png")
            if screen.capture_to(path) is None:
                return ToolResult(
                    False,
                    "I couldn't take a screenshot. On Wayland I need grim or spectacle; on X11 scrot or ImageMagick's "
                    "import. The desktop may also be refusing screen capture.",
                )
            client = self.client or LLMClient(
                base_url=llm.get("base_url", "http://localhost:11434/v1"),
                model=model,
                api_key=llm.get("api_key", ""),
                timeout=int(llm.get("timeout", 120)),
            )
            try:
                reply = client.complete_vision(str(question or DEFAULT_QUESTION), path, model=model or None)
            except Exception as exc:
                return ToolResult(False, f"I took the screenshot but couldn't ask the vision model: {exc}")
        try:
            text = str(reply["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError):
            text = ""
        if not text:
            return ToolResult(False, "The vision model answered with nothing.")
        return ToolResult(True, text, {"model": model or "the configured model", "stored": False})

    def preview(self, question: str = DEFAULT_QUESTION, **_: Any) -> dict[str, Any] | None:
        llm = self.config.get("llm", {}) if isinstance(self.config, dict) else {}
        return {
            "screen": f"one screenshot, then: {question or DEFAULT_QUESTION}",
            "the picture goes to": _where_it_goes(llm),
        }


def _where_it_goes(llm: dict[str, Any]) -> str:
    """Say plainly who gets to see the screenshot, so the user can say no to a model that isn't on this computer."""
    model = str(llm.get("vision_model") or "").strip()
    if not model:
        return "nobody - no vision model is set, so nothing will be taken"
    host = (urlparse(str(llm.get("base_url") or "http://localhost:11434/v1")).hostname or "").lower()
    if host in LOCAL_HOSTS:
        return f"the vision model {model} on this computer; it is deleted right after"
    return f"the vision model {model} at {host or 'a server'}, which is NOT this computer; it is deleted here right after"
