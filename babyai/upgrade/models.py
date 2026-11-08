"""Upgrading his brain: see which models the server has, pull a better one, and switch to it.

Pulling and listing use Ollama's own API (the root of ``[llm] base_url``, without the ``/v1``); a server that only
speaks the OpenAI-style API can still be switched to a model by name. A model is tried with a tiny request before
the switch, and a model that can't use tools is refused (he would lose his hands). The new model is kept for the
session and written to ``config.toml`` so it is still the one next time.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from babyai.llm.client import LLMClient
from babyai.upgrade.gitops import UpgradeError

MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-:/]{0,100}$")
PULL_TIMEOUT_S = 3600  # a big model takes a while
TRY_TIMEOUT_S = 300  # the first request has to load it into memory


def ollama_root(base_url: str) -> str:
    root = base_url.rstrip("/")
    return root[: -len("/v1")] if root.endswith("/v1") else root


def set_toml_value(text: str, table: str, key: str, value: str) -> str:
    """``key = "value"`` inside ``[table]`` of a TOML text: replaced if it is there, added if not (the table too)."""
    line = f"{key} = {json.dumps(value)}"  # JSON string escapes are valid TOML: a value can never break out of its quotes
    lines = text.splitlines()
    header = re.compile(rf"^\s*\[{re.escape(table)}\]\s*(#.*)?$")
    any_header = re.compile(r"^\s*\[")
    start = next((i for i, current in enumerate(lines) if header.match(current)), None)
    if start is None:
        return text.rstrip("\n") + ("\n\n" if text.strip() else "") + f"[{table}]\n{line}\n"
    end = next((i for i in range(start + 1, len(lines)) if any_header.match(lines[i])), len(lines))
    for i in range(start + 1, end):
        if re.match(rf"^\s*{re.escape(key)}\s*=", lines[i]):
            lines[i] = line
            break
    else:
        lines.insert(start + 1, line)
    return "\n".join(lines) + "\n"


def persist_model(config_path: str, model: str) -> str:
    """Write ``[llm] model`` into the user's config.toml (keeping a .bak of what was there). Returns what was done."""
    path = Path(config_path)
    old = path.read_text() if path.exists() else ""
    if old and not path.with_name(path.name + ".bak").exists():
        path.with_name(path.name + ".bak").write_text(old)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(set_toml_value(old, "llm", "model", model))
    os.replace(temp, path)
    return f"saved to {path}"


class ModelUpgrades:
    def __init__(self, assistant: Any, config_path: str | None = None) -> None:
        self.assistant = assistant
        self.config_path = config_path

    # ------------------------------------------------------------------ the server

    @property
    def _client(self) -> LLMClient:
        return self.assistant.llm_client

    def _call(self, method: str, path: str, body: dict | None = None, timeout: float = 15) -> Any:
        root = ollama_root(getattr(self._client, "base_url", "http://localhost:11434/v1"))
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(root + path, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            raise UpgradeError(f"The model server answered {exc.code} to {path}: {exc.read().decode(errors='replace')[:200]}") from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise UpgradeError(f"I couldn't reach the model server at {root}: {getattr(exc, 'reason', exc)}") from None

    def installed(self) -> list[dict[str, Any]]:
        """[{name, size, tools}] - tools is True/False if the server says, None if it doesn't."""
        try:
            tags = self._call("GET", "/api/tags").get("models", [])
        except UpgradeError:
            lister = getattr(self._client, "list_models", None)
            names = lister() if callable(lister) else []
            if not names:
                raise
            return [{"name": name, "size": 0, "tools": None} for name in names]
        out = []
        for entry in tags:
            caps = entry.get("capabilities")
            out.append({"name": entry.get("name", ""), "size": int(entry.get("size") or 0), "tools": ("tools" in caps) if isinstance(caps, list) else None})
        return out

    def _check_name(self, name: str) -> str:
        name = (name or "").strip()
        if not MODEL_NAME.match(name):
            raise UpgradeError("Give a model name like llama3.1:8b (letters, digits and . _ - : / only).")
        return name

    # ------------------------------------------------------------------ actions

    def list(self) -> str:
        models = self.installed()
        current = getattr(self._client, "model", "?")
        rows = [
            f"{m['name']}" + (f" ({m['size'] / 1e9:.1f} GB)" if m["size"] else "")
            + {True: ", can use tools", False: ", CAN'T use tools", None: ""}[m["tools"]]
            + (" <- in use" if m["name"] == current else "")
            for m in models
        ]
        return f"The server has {len(models)} model(s); I'm using {current}:\n" + "\n".join(rows)

    def preview_pull(self, model: str) -> dict[str, str]:
        return {"download": self._check_name(model), "from": ollama_root(self._client.base_url), "note": "this can be several GB"}

    def pull(self, model: str) -> str:
        name = self._check_name(model)
        self._call("POST", "/api/pull", {"model": name, "stream": False}, timeout=PULL_TIMEOUT_S)
        return f"Pulled {name}. Switch to it with action=switch, model={name}."

    def preview_switch(self, model: str) -> dict[str, str]:
        return {"switch to": self._check_name(model), "from": str(getattr(self._client, "model", "?")), "first": "I try it with a short request", "kept": "in config.toml too"}

    def switch(self, model: str) -> str:
        name = self._check_name(model)
        have = {m["name"]: m for m in self.installed()}
        match = name if name in have else (f"{name}:latest" if f"{name}:latest" in have else None)
        if match is None:
            raise UpgradeError(f"{name} isn't on the server (it has: {', '.join(sorted(have)[:8]) or 'nothing'}). Pull it first with action=pull.")
        if have[match]["tools"] is False:
            raise UpgradeError(f"{match} can't use tools, and I'd lose the ability to do things for you, so I won't switch to it.")
        trial = LLMClient(
            base_url=self._client.base_url, model=match, api_key=getattr(self._client, "api_key", ""), timeout=TRY_TIMEOUT_S,
        )
        try:
            reply = trial.complete(messages=[{"role": "user", "content": "Reply with the single word: ready"}], tools=None, stream=False)
        except Exception as exc:
            raise UpgradeError(f"I tried {match} and it didn't work ({str(exc)[:160]}), so I stayed on the current model.") from None
        if not str(reply.get("choices", [{}])[0].get("message", {}).get("content") or "").strip():
            raise UpgradeError(f"{match} answered with nothing, so I stayed on the current model.")
        previous = self._client.model
        self._client.model = match
        self.assistant._tools_ok = True
        kept = "only for this session (I don't know where config.toml is)"
        if self.config_path:
            try:
                kept = persist_model(self.config_path, match)
            except OSError as exc:
                kept = f"only for this session (couldn't write the config: {exc})"
        return f"Switched from {previous} to {match}; {kept}."
