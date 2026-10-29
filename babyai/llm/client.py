import json
import urllib.error
import urllib.request
from typing import Any


class LLMClient:
    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def complete(self, messages: list[dict[str, str]], tools: list[dict[str, Any]] | None = None, stream: bool = False) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools

        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"LLM request failed: {exc.code} {exc.reason}") from exc
        except Exception as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc

        if not stream:
            return json.loads(body)

        return self._parse_stream_response(body)

    def _parse_stream_response(self, body: str) -> dict[str, Any]:
        events = []
        for line in body.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                continue

        assistant_message = {"role": "assistant", "content": ""}
        tool_calls: list[dict[str, Any]] = []

        for event in events:
            choices = event.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            if delta.get("content"):
                assistant_message["content"] += delta["content"]
            for tool_call in delta.get("tool_calls", []) or []:
                idx = tool_call.get("index", 0)
                while len(tool_calls) <= idx:
                    tool_calls.append({})
                current = tool_calls[idx]
                current.setdefault("id", tool_call.get("id", ""))
                current.setdefault("type", tool_call.get("type", "function"))
                function = current.setdefault("function", {"name": "", "arguments": ""})
                if tool_call.get("function", {}).get("name"):
                    function["name"] = tool_call["function"]["name"]
                if tool_call.get("function", {}).get("arguments"):
                    function["arguments"] += tool_call["function"]["arguments"]

        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
        return {"choices": [{"message": assistant_message}]}
