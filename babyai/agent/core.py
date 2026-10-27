import json
from typing import Any

from babyai.agent.context import ConversationContext, DEFAULT_SYSTEM_PROMPT
from babyai.config.loader import load_config
from babyai.llm.client import LLMClient
from babyai.memory.store import MemoryStore
from babyai.safety.gate import SafetyGate
from babyai.tools.registry import build_default_registry


class BabyAI:
    def __init__(self, config_path: str = "config.toml", voice_enabled: bool = False):
        self.config = load_config(config_path)
        self.voice_enabled = voice_enabled or bool(self.config.get("voice", {}).get("enabled", False))

        memory_path = self.config.get("memory", {}).get("db_path", "~/.local/share/babyai/memory.db")
        self.memory = MemoryStore(memory_path)
        self.safety_gate = SafetyGate(level=self.config.get("safety", {}).get("level", "strict"))
        self.context = ConversationContext(self.config.get("agent", {}).get("system_prompt", DEFAULT_SYSTEM_PROMPT))
        self.tool_registry = build_default_registry(memory_store=self.memory, config=self.config)

        llm_cfg = self.config.get("llm", {})
        self.llm_client = LLMClient(
            base_url=llm_cfg.get("base_url", "http://localhost:11434/v1"),
            model=llm_cfg.get("model", "llama-3.1-8b"),
            api_key=llm_cfg.get("api_key", ""),
        )

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
            outputs: list[str] = []
            for call in tool_calls:
                func = call.get("function", {})
                name = func.get("name")
                args = func.get("arguments", "")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                result_obj = self.tool_registry.call(name=name, arguments=args, interactive=True)
                outputs.append(f"{name}: {result_obj.content if hasattr(result_obj, 'content') else str(result_obj)}")
            assistant_reply = "\n".join(outputs)
            self.context.add_assistant_message(assistant_reply)
            return assistant_reply

        content = message.get("content") or "I’m here and ready to help."
        self.context.add_assistant_message(content)
        return content

    def _fallback_response(self, user_input: str) -> str:
        lowered = user_input.lower()
        if "hello" in lowered or "hi" in lowered:
            return "Hello! I am BabyAI, running in text-only mode."
        if "remember" in lowered or "memory" in lowered:
            return "I can remember facts and search my local SQLite memory store."
        if "list" in lowered and "file" in lowered:
            return "I can list files with the list_directory tool when available."
        return "I am BabyAI and I am ready to help in text mode."
