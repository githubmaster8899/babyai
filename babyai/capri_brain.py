"""Let BabyAI be the brain of the Capri desktop companion, without changing Capri.

On Linux and macOS the Capri app (capri/, built separately) only knows one kind of brain: an Ollama
server at ``127.0.0.1:11434``. This module serves the small part of Ollama's HTTP API that Capri uses
and answers it with BabyAI: the LLM from BabyAI's ``[llm]`` config (any OpenAI-compatible server, not
just Ollama), BabyAI's memory, and BabyAI's safety gate. Capri keeps her own look, voice, personality
and job tools; only the thinking comes from BabyAI.

What BabyAI adds on top of passing messages through:

* **Model** - replies come from ``[llm] base_url / model / api_key``. Capri's model choice is ignored.
* **Memory** - facts from BabyAI's memory store that match what the user just said are added to
  Capri's chat prompt, and Capri's ``remember`` note-taking is mirrored into the same store.
* **Safety** - tool calls the brain returns go through ``SafetyGate``. By default that stops Capri's
  ``power`` tool from shutting down, restarting or signing out. This covers what the *brain* returns:
  Capri also recognises many plain commands herself and runs them without asking the brain, which
  nothing in this module can intercept.

The server only listens on the loopback interface and refuses browser (cross-site) requests.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterator

from babyai.config.loader import load_config
from babyai.llm.client import LLMClient
from babyai.memory.store import MemoryStore
from babyai.safety.gate import SafetyGate

MODEL_NAME = "babyai:latest"  # the one "model" offered to Capri, so her menu shows where her brain is from
DEFAULT_PORT = 11434  # Capri's address for Ollama is fixed in her code
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MAX_BODY = 32 * 1024 * 1024  # screenshots arrive as base64, so requests can be big
UPSTREAM_TIMEOUT = 180  # seconds; Capri gives up on her side after the same time
RECALL_LIMIT = 5
BLOCK_NOTE_SECONDS = 60.0

# Capri reads these tool arguments as strings, so a model that sends 80 instead of "80" would break her.
STRING_ARGS = (
    "name", "site", "search", "action", "focus", "song", "app", "kind", "task", "target", "text",
    "keys", "button", "enter", "direction", "seconds", "minutes", "level", "scroll_direction",
)
STOP_WORDS = frozenset(
    "the and for you your yours with that this what have has had are was were can could would should "
    "not but how who why when where which will just about from into they them their there then than "
    "also like some any all one get got let its our out too very".split()
)
_WORD = re.compile(r"[^\W_]{3,}")


class BrainError(Exception):
    """Something to report to Capri as an Ollama-style ``{"error": ...}`` reply."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- translation


def search_terms(text: str, limit: int = 8) -> list[str]:
    seen: list[str] = []
    for word in _WORD.findall((text or "").lower()):
        if word not in STOP_WORDS and word not in seen:
            seen.append(word)
    return seen[:limit]


def fts_query(text: str, joiner: str = " OR ") -> str:
    """A safe full-text query for BabyAI's memory: plain quoted words, so punctuation cannot break it."""
    return joiner.join(f'"{term}"' for term in search_terms(text))


def to_openai_messages(messages: list[Any]) -> list[dict[str, Any]]:
    """Ollama chat messages -> OpenAI chat messages (images become data-URL parts)."""
    converted: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user")
        content = message.get("content")
        images = [image for image in (message.get("images") or []) if isinstance(image, str) and image]
        item: dict[str, Any] = {"role": role}
        if images and isinstance(content, (str, type(None))):
            parts: list[dict[str, Any]] = [{"type": "text", "text": content or ""}]
            parts += [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}} for image in images]
            item["content"] = parts
        else:
            item["content"] = content if content is not None else ""
        calls = message.get("tool_calls")
        if role == "assistant" and isinstance(calls, list) and calls:
            item["tool_calls"] = [
                {
                    "id": f"call_{index}_{number}",
                    "type": "function",
                    "function": {
                        "name": str((call.get("function") or {}).get("name") or ""),
                        "arguments": json.dumps((call.get("function") or {}).get("arguments") or {}),
                    },
                }
                for number, call in enumerate(calls)
                if isinstance(call, dict)
            ]
        converted.append(item)
    return converted


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        if value is None:
            continue
        if key in STRING_ARGS and isinstance(value, (bool, int, float)):
            value = json.dumps(value) if isinstance(value, bool) else str(value)
        cleaned[key] = value
    return cleaned


def read_openai_reply(result: dict[str, Any]) -> tuple[str, list[tuple[str, dict[str, Any]]], str]:
    """(text, [(tool name, arguments)], Ollama-style done_reason) from an OpenAI chat completion."""
    choices = result.get("choices") if isinstance(result, dict) else None
    if not choices or not isinstance(choices[0], dict):
        raise BrainError("The model sent no reply.")
    message = choices[0].get("message") or {}
    calls: list[tuple[str, dict[str, Any]]] = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") if isinstance(call, dict) else None
        if isinstance(function, dict) and function.get("name"):
            calls.append((str(function["name"]), _arguments(function.get("arguments"))))
    reason = "length" if choices[0].get("finish_reason") == "length" else "stop"
    return str(message.get("content") or ""), calls, reason


def ollama_reply(content: str, calls: list[tuple[str, dict[str, Any]]] | None = None, reason: str = "stop") -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [{"function": {"name": name, "arguments": args}} for name, args in calls]
    return {
        "model": MODEL_NAME,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "message": message,
        "done": True,
        "done_reason": reason,
    }


# ---------------------------------------------------------------------- brain


class CapriBrain:
    """BabyAI's thinking for Capri: no HTTP in here, so it can be tested directly."""

    def __init__(self, llm: LLMClient, memory_path: str, gate: SafetyGate, clock: Callable[[], float] = time.monotonic):
        self.llm = llm
        self.memory_path = memory_path
        self.gate = gate
        self._clock = clock
        self._lock = threading.Lock()
        self._blocked: tuple[float, str] | None = None

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "CapriBrain":
        llm = config.get("llm", {})
        return cls(
            LLMClient(
                base_url=llm.get("base_url", "http://localhost:11434/v1"),
                model=llm.get("model", "llama-3.1-8b"),
                api_key=llm.get("api_key", ""),
                timeout=UPSTREAM_TIMEOUT,
            ),
            memory_path=config.get("memory", {}).get("db_path", "~/.local/share/babyai/memory.db"),
            gate=SafetyGate(level=config.get("safety", {}).get("level", "strict")),
        )

    def tags(self) -> dict[str, Any]:
        return {"models": [{"name": MODEL_NAME, "model": MODEL_NAME}]}

    def chat(self, request: dict[str, Any]) -> dict[str, Any]:
        messages = request.get("messages")
        if not isinstance(messages, list):
            raise BrainError("messages must be a list", 400)
        if not messages:  # Capri's "load the model" warm-up: nothing to think about
            return ollama_reply("", reason="load")
        tools = request.get("tools") if isinstance(request.get("tools"), list) and request.get("tools") else None
        converted = to_openai_messages(messages)
        if tools is None:  # plain chat; the tool-choosing call is left exactly as Capri wrote it
            self._add_context(converted)
        try:
            result = self.llm.complete(messages=converted, tools=tools, stream=False, extra=self._sampling(request))
        except Exception as exc:  # urllib / JSON problems from the upstream model server
            raise BrainError(str(exc)) from exc
        content, calls, reason = read_openai_reply(result)
        if calls:
            content, calls = self._screen(content, calls)
        return ollama_reply(content, calls, reason)

    # -- helpers

    @staticmethod
    def _sampling(request: dict[str, Any]) -> dict[str, Any]:
        options = request.get("options") if isinstance(request.get("options"), dict) else {}
        extra: dict[str, Any] = {}
        if isinstance(options.get("num_predict"), int) and options["num_predict"] > 0:
            extra["max_tokens"] = options["num_predict"]
        if isinstance(options.get("temperature"), (int, float)):
            extra["temperature"] = options["temperature"]
        return extra

    @contextlib.contextmanager
    def _memory(self) -> Iterator[MemoryStore | None]:
        # One short-lived connection per use: SQLite connections cannot be shared between the server's threads.
        try:
            store = MemoryStore(self.memory_path)
        except (OSError, sqlite3.Error) as exc:
            print(f"[babyai-brain] memory unavailable: {exc}", file=sys.stderr)
            yield None
            return
        try:
            yield store
        except sqlite3.Error as exc:
            print(f"[babyai-brain] memory error: {exc}", file=sys.stderr)
        finally:
            store.close()

    def _add_context(self, converted: list[dict[str, Any]]) -> None:
        """Append what BabyAI remembers (and any just-blocked action) to Capri's system prompt."""
        if any(isinstance(item.get("content"), list) for item in converted):
            return  # a screenshot question: leave it alone
        extra = ""
        user_text = next((m["content"] for m in reversed(converted) if m["role"] == "user" and isinstance(m["content"], str)), "")
        query = fts_query(user_text)
        if query:
            facts: list[dict[str, Any]] = []
            with self._memory() as store:  # a database error inside this block is reported and skipped
                facts = store.recall_memory(query, limit=RECALL_LIMIT) if store else []
            if facts:
                lines = "\n".join(f"- {fact['fact']}" for fact in facts)
                extra += f"\nBabyAI's memory has these notes about them (use them naturally, don't recite them):\n{lines}"
        with self._lock:
            blocked, self._blocked = self._blocked, None
        if blocked and self._clock() - blocked[0] <= BLOCK_NOTE_SECONDS:
            extra += (
                f"\nBabyAI's safety gate just stopped you from doing this: {blocked[1]}. You did NOT do it: "
                "tell them so in one short sentence, and that it is blocked by BabyAI's safety.level setting."
            )
        if not extra:
            return
        for item in converted:
            if item["role"] == "system" and isinstance(item["content"], str):
                item["content"] += extra
                return
        converted.insert(0, {"role": "system", "content": extra.strip()})

    def _screen(self, content: str, calls: list[tuple[str, dict[str, Any]]]) -> tuple[str, list[tuple[str, dict[str, Any]]]]:
        kept: list[tuple[str, dict[str, Any]]] = []
        stopped: list[str] = []
        can_relax = False
        for name, args in calls:
            with self._lock:
                verdict = self.gate.check(name, args, interactive=True)
            if verdict["allowed"]:
                kept.append((name, args))
                continue
            what = f"{name} {args.get('action', '')}".strip()
            if verdict["decision"] == "needs_confirmation":  # Capri's app has no way to ask, so it is stopped
                can_relax = True
                stopped.append(f'{what} (safety.level is "{self.gate.level}", which wants confirmation first)')
            else:
                stopped.append(f"{what} ({verdict['reason']})")
        self._mirror_notes(kept)
        if stopped:
            with self._lock:
                self._blocked = (self._clock(), "; ".join(stopped))
            if not kept and not content.strip():
                content = f"BabyAI's safety gate blocked that: {'; '.join(stopped)}."
                if can_relax:
                    content += ' Set safety.level to "relaxed" in config.toml to allow it.'
        return content, kept

    def _mirror_notes(self, calls: list[tuple[str, dict[str, Any]]]) -> None:
        """Capri's own notes live in her me.md; keep BabyAI's memory in step with what she is told to remember."""
        for name, args in calls:
            text = str(args.get("text") or "").strip()[:500]
            if name != "remember" or not text:
                continue
            if str(args.get("action") or "remember").lower() == "forget":
                with self._lock:
                    allowed = self.gate.check("forget_memory", {"query": text}, interactive=True)["allowed"]
                query = fts_query(text, joiner=" AND ")
                if allowed and query:
                    with self._memory() as store:
                        if store:
                            store.forget_memory(query=query)
                continue
            with self._memory() as store:
                if store and not any(f["fact"].lower() == text.lower() for f in self._similar(store, text)):
                    store.remember_fact(text, source="capri")

    @staticmethod
    def _similar(store: MemoryStore, text: str) -> list[dict[str, Any]]:
        query = fts_query(text)
        return store.recall_memory(query, limit=20) if query else []


# ----------------------------------------------------------------------- HTTP


def _host_ok(value: str | None) -> bool:
    if value is None:  # an HTTP/1.0 client with no Host header
        return True
    return (urllib.parse.urlsplit("//" + value).hostname or "").lower() in LOCAL_HOSTS


def _origin_ok(value: str | None) -> bool:
    if value is None:  # not a browser: Capri and other programs send no Origin
        return True
    parts = urllib.parse.urlsplit(value)  # "null" (sandboxed pages, file:// pages) has no host and is refused
    return parts.scheme in ("http", "https") and (parts.hostname or "").lower() in LOCAL_HOSTS


class _Handler(BaseHTTPRequestHandler):
    server_version = "BabyAIBrain/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # the per-request chat summary below replaces this
        pass

    def _send(self, status: int, body: Any) -> None:
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8" if isinstance(body, str) else "application/json")
        self.send_header("Content-Length", str(len(data)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self) -> bool:
        # Blocks DNS-rebinding (odd Host) and web pages posting to localhost (they always send an Origin).
        if _host_ok(self.headers.get("Host")) and _origin_ok(self.headers.get("Origin")):
            return True
        self.close_connection = True  # the request body was not read, so the connection cannot be reused
        self._send(403, {"error": "forbidden"})
        return False

    def do_GET(self) -> None:
        if not self._allowed():
            return
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, "BabyAI is Capri's brain")
        elif path == "/api/tags":
            self._send(200, self.server.brain.tags())  # type: ignore[attr-defined]
        elif path == "/api/version":
            self._send(200, {"version": "0.0.0-babyai"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._allowed():
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self.close_connection = True
            self._send(413, {"error": "request too large"})
            return
        raw = self.rfile.read(length)
        path = self.path.split("?", 1)[0]
        try:
            body = json.loads(raw.decode("utf-8")) if raw.strip() else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(400, {"error": "invalid JSON"})
            return
        if not isinstance(body, dict):
            self._send(400, {"error": "expected a JSON object"})
            return
        if path == "/api/pull":  # Capri "downloads" her model; there is nothing to download
            self._send(200, {"status": "success"})
        elif path == "/api/chat":
            started = time.monotonic()
            try:
                reply = self.server.brain.chat(body)  # type: ignore[attr-defined]
            except BrainError as exc:
                self.server.log(f"chat failed: {exc}")  # type: ignore[attr-defined]
                self._send(exc.status, {"error": str(exc)})
                return
            calls = reply["message"].get("tool_calls") or []
            names = ", ".join(call["function"]["name"] for call in calls) or "none"
            self.server.log(  # type: ignore[attr-defined]
                f"chat: {len(body.get('messages') or [])} messages, tools {'offered' if body.get('tools') else 'not offered'}, "
                f"tool calls: {names}, {time.monotonic() - started:.1f}s"
            )
            self._send(200, reply)
        else:
            self._send(404, {"error": "not found"})


class BrainServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], brain: CapriBrain, log: Callable[[str], None] | None = None):
        super().__init__(address, _Handler)
        self.brain = brain
        self.log = log or (lambda line: print(f"[babyai-brain] {line}", file=sys.stderr))


def points_at_self(base_url: str, port: int) -> bool:
    """True if BabyAI's own LLM address is the port this server is about to take (it would talk to itself)."""
    parsed = urllib.parse.urlsplit(base_url)
    host = (parsed.hostname or "").lower()
    their_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return their_port == port and (host in LOCAL_HOSTS or host == "0.0.0.0")


def serve_capri(config_path: str = "config.toml", port: int = DEFAULT_PORT, launch: bool = False) -> int:
    """Run the brain until Ctrl-C (or, with ``launch``, until the Capri app it started exits)."""
    config = load_config(config_path)
    base_url = config.get("llm", {}).get("base_url", "")
    if points_at_self(base_url, port):
        print(
            f"BabyAI's own model address ({base_url}) is the port Capri's brain has to use ({port}).\n"
            f"Move your model server (for Ollama: OLLAMA_HOST=127.0.0.1:{port + 1} ollama serve) and set\n"
            f'[llm] base_url = "http://127.0.0.1:{port + 1}/v1" and model = "<your model>" in {config_path}.'
        )
        return 2
    try:
        server = BrainServer(("127.0.0.1", port), CapriBrain.from_config(config))
    except OSError as exc:
        print(f"Could not listen on 127.0.0.1:{port}: {exc}\nIs Ollama (or another BabyAI brain) already running there?")
        return 2

    llm = config.get("llm", {})
    print(f"BabyAI is Capri's brain on 127.0.0.1:{port}, thinking with {llm.get('model')} at {base_url}.")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    process = None
    try:
        if launch:
            from babyai.capri_launcher import launch_capri

            result = launch_capri()
            print(result.message)
            process = result.process if result.ok else None
            if process is None:
                print("Keeping the brain running; start Capri yourself (python3 -m babyai --capri).")
        print("Press Ctrl-C to stop." if process is None else "Stops when Capri closes (or press Ctrl-C).")
        while process is None or process.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
    return 0
