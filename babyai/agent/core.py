import json
import os
import platform
import subprocess
import sys
from typing import Any

from babyai.agent.context import ConversationContext
from babyai.config.loader import load_config
from babyai.llm.client import LLMClient
from babyai.memory.store import MemoryStore
from babyai.safety.gate import SafetyGate
from babyai.tools.registry import build_default_registry


class BabyAI:
    def __init__(self, config_path: str = "config.toml", voice_enabled: bool = False):
        self.config = load_config(config_path)
        self.voice_enabled = voice_enabled or bool(self.config.get("voice", {}).get("enabled", False))
        self.memory = MemoryStore(self.config.get("memory", {}).get("db_path", "~/.local/share/babyai/memory.db"))
        self.safety_gate = SafetyGate(level=self.config.get("safety", {}).get("level", "strict"))
        self.context = ConversationContext(self.config.get("agent", {}).get("system_prompt", DEFAULT_SYSTEM_PROMPT))
        self.tool_registry = build_default_registry(memory_store=self.memory, config=self.config)
        base_url = self.config.get("llm", {}).get("base_url", "http://localhost:11434/v1")
        model = self.config.get("llm", {}).get("model", "llama-3.1-8b")
        api_key = self.config.get("llm", {}).get("api_key", "")
        self.llm_client = LLMClient(base_url=base_url, model=model, api_key=api_key)

    def run_cli(self) -> None:
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
        tools = self.tool_registry.get_openai_tools()

        if self.llm_client is None:
            reply = self._fallback_response(user_input)
            self.context.add_assistant_message(reply)
            return reply

        try:
            response = self.llm_client.complete(messages=messages, tools=tools, stream=False)
        except Exception as exc:  # pragma: no cover - runtime fallback for offline LLMs
            reply = f"I could not reach the configured LLM endpoint: {exc}. Please check your config."
            self.context.add_assistant_message(reply)
            return reply

        assistant_message = response.get("message", {})
        tool_calls = assistant_message.get("tool_calls") or []

        if tool_calls:
            tool_outputs: list[str] = []
            for call in tool_calls:
                function = call.get("function", {})
                name = function.get("name")
                arguments = function.get("arguments", "")
                if isinstance(arguments, str):
                    arguments = json.loads(arguments) if arguments else {}
                result = self.tool_registry.call(
                    name=name,
                    arguments=arguments,
                    interactive=True,
                    user_confirmation=None,
                )
                tool_outputs.append(f"{name}: {result.content}")
            final_reply = "\n".join(tool_outputs)
            self.context.add_assistant_message(final_reply)
            return final_reply

        content = assistant_message.get("content") or "I’m here and ready to help."
        self.context.add_assistant_message(content)
        return content

    def _fallback_response(self, user_input: str) -> str:
        lowered = user_input.lower()
        if "hello" in lowered or "hi" in lowered:
            return "Hello! I am BabyAI, running in offline text mode."
        if "memory" in lowered:
            return "I can remember facts and search my local SQLite memory store."
        if "list" in lowered and "file" in lowered:
            return "I can list files using the list_directory tool when available."
        return "I am BabyAI and I am ready to help in text mode."
