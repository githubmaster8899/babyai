import json
import sys
from typing import Any, Callable

from babyai.agent.context import ConversationContext, DEFAULT_SYSTEM_PROMPT
from babyai.config.loader import load_config
from babyai.llm.client import LLMClient
from babyai.memory.store import MemoryStore
from babyai.safety.gate import SafetyGate
from babyai.tools.registry import build_default_registry


# Asked when the safety gate wants the user's say-so: (tool name, arguments, gate verdict) -> allowed?
ConfirmHook = Callable[[str, dict[str, Any], dict[str, Any]], bool]

MAX_TOOL_CALLS = 4  # tool calls honoured per user message
MAX_TOOL_OUTPUT = 4000  # characters of a tool result handed back to the model


class BabyAI:
    def __init__(self, config_path: str = "config.toml", voice_enabled: bool = False):
        self.config = load_config(config_path)
        self.voice_enabled = voice_enabled or bool(self.config.get("voice", {}).get("enabled", False))

        memory_path = self.config.get("memory", {}).get("db_path", "~/.local/share/babyai/memory.db")
        self.memory = MemoryStore(memory_path)
        self.safety_gate = SafetyGate(level=self.config.get("safety", {}).get("level", "strict"))
        self.context = ConversationContext(self.config.get("agent", {}).get("system_prompt", DEFAULT_SYSTEM_PROMPT))
        self.tool_registry = build_default_registry(memory_store=self.memory, config=self.config)

        # Set by the front end (CLI prompt, desktop bubble). Without one, anything the
        # safety gate wants confirmed is declined rather than silently run.
        self.confirm: ConfirmHook | None = None

        llm_cfg = self.config.get("llm", {})
        self.llm_client = LLMClient(
            base_url=llm_cfg.get("base_url", "http://localhost:11434/v1"),
            model=llm_cfg.get("model", "llama-3.1-8b"),
            api_key=llm_cfg.get("api_key", ""),
        )

    def cli_confirm(self, name: str, arguments: dict[str, Any], verdict: dict[str, Any]) -> bool:
        shown = ", ".join(f"{key}={str(value)[:80]!r}" for key, value in arguments.items()) or "no arguments"
        print(f"BabyAI wants to run {name} ({verdict.get('risk_level')}): {shown}")
        try:
            return input("Allow? [y/N] ").strip().lower() in {"y", "yes"}
        except EOFError:
            return False

    def run_cli(self) -> None:
        if self.confirm is None and sys.stdin.isatty():
            self.confirm = self.cli_confirm
        print("BabyAI starting. Type 'exit' to quit.")
        while True:
            try:
                user_input = input("You> ")
            except EOFError:
                print()
                break
            if user_input.strip().lower() in {"exit", "quit", "bye"}:
                print("BabyAI: Goodbye.")
                break
            response = self.process_user_input(user_input)
            print(response)

    def process_user_input(self, user_input: str) -> str:
        self.context.add_user_message(user_input)
        messages = self.context.build_messages()
        tool_schemas = self.tool_registry.get_openai_tools()

        try:
            result = self.llm_client.complete(messages=messages, tools=tool_schemas, stream=False)
        except Exception:
            fallback = self._fallback_response(user_input)
            self.context.add_assistant_message(fallback)
            return fallback

        message = result.get("choices", [{}])[0].get("message", {})
        tool_calls = message.get("tool_calls") or []

        if tool_calls:
            calls = []
            for index, call in enumerate(tool_calls[:MAX_TOOL_CALLS]):
                name, args = self._parse_call(call)
                calls.append((call.get("id") or f"call_{index}", name, args, self._run_tool(name, args)))
            assistant_reply = self._narrate(messages, message, calls)
            if not assistant_reply:  # no follow-up from the model: show what the tools said
                assistant_reply = "\n".join(f"{name}: {text}" for _id, name, _args, text in calls)
            self.context.add_assistant_message(assistant_reply)
            return assistant_reply

        content = message.get("content") or "I’m here and ready to help."
        self.context.add_assistant_message(content)
        return content

    @staticmethod
    def _parse_call(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        func = call.get("function") or {}
        args = func.get("arguments", "")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = {}
        return str(func.get("name") or ""), args if isinstance(args, dict) else {}

    def _run_tool(self, name: str, args: dict[str, Any]) -> str:
        """Run one tool call through the safety gate. Always returns text for the model; never raises."""
        if self.tool_registry.get(name) is None:
            return f"There is no tool called {name!r}."
        verdict = self.safety_gate.check(name, args, interactive=True)
        if not verdict["allowed"]:
            if not verdict["needs_confirmation"]:
                return f"Blocked by the safety gate: {verdict['reason']}."
            approved = self._ask_confirmation(name, args, verdict)
            self.safety_gate.record_confirmation(name, args, approved)
            if not approved:
                return f"The user declined to let {name} run."
        try:
            result = self.tool_registry.call(name=name, arguments=args, interactive=True)
        except TypeError as exc:  # the model passed arguments the tool does not take
            return f"{name} was called with bad arguments: {exc}"
        except Exception as exc:
            return f"{name} failed: {exc}"
        if isinstance(result, dict):
            text = str(result.get("content", ""))
        else:
            text = str(getattr(result, "content", result))
        return text[:MAX_TOOL_OUTPUT]

    def _ask_confirmation(self, name: str, args: dict[str, Any], verdict: dict[str, Any]) -> bool:
        if self.confirm is None:
            return False
        try:
            return bool(self.confirm(name, args, verdict))
        except Exception:
            return False

    def _narrate(self, messages: list[dict[str, Any]], message: dict[str, Any], calls: list[tuple]) -> str:
        """Second model pass: hand the tool results back so the reply is in the assistant's own voice."""
        followup = list(messages)
        followup.append({
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": [
                {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
                for call_id, name, args, _text in calls
            ],
        })
        for call_id, _name, _args, text in calls:
            followup.append({"role": "tool", "tool_call_id": call_id, "content": text})
        try:
            result = self.llm_client.complete(messages=followup, tools=None, stream=False)
            reply = result.get("choices", [{}])[0].get("message", {})
        except Exception:
            return ""
        return str(reply.get("content") or "").strip()

    def _fallback_response(self, user_input: str) -> str:
        lowered = user_input.lower()
        if "hello" in lowered or "hi" in lowered:
            return "Hello! I am BabyAI, running in text-only mode."
        if "remember" in lowered or "memory" in lowered:
            return "I can remember facts and search my local SQLite memory store."
        if "list" in lowered and "file" in lowered:
            return "I can list files with the list_directory tool when available."
        return "I am BabyAI and I am ready to help in text mode."
