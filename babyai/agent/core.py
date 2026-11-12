import json
import os
import re
import sys
import threading
from typing import Any, Callable

from babyai.agent.context import ConversationContext, DEFAULT_SYSTEM_PROMPT
from babyai.config.loader import load_config
from babyai.llm.client import LLMClient
from babyai.habits import Habits
from babyai.memory.store import MemoryStore
from babyai.personality import Personality, defaults_from_config
from babyai.safety.gate import SafetyGate
from babyai.tools.registry import build_default_registry
from babyai.upgrade import UpgradeContext


# Asked when the safety gate wants the user's say-so: (tool name, arguments, gate verdict) -> allowed?
ConfirmHook = Callable[[str, dict[str, Any], dict[str, Any]], bool]

MAX_TOOL_CALLS = 4  # tool calls honoured per round
MAX_ROUNDS = 6  # model <-> tools round trips per message - but only while he is working on himself (upgrade_* tools); otherwise one
MAX_TOOL_CALLS_TOTAL = 12  # tool calls across all rounds of one message
MAX_TOOL_OUTPUT = 4000  # characters of a tool result handed back to the model

# Small models often write a tool call out as text - {"name": "ask_user", "parameters": {...}} - instead of using the
# tool_calls field, and a slightly broken one (a stray quote, say) isn't converted by the server either.
_TEXT_CALL = re.compile(r'^\s*(?:```(?:json)?\s*)?\{\s*"name"\s*:\s*"(?P<name>[\w.\-]+)"', re.DOTALL)
_TEXT_ARGS = re.compile(r'"(?:parameters|arguments|args)"\s*:\s*(?=\{)')


def text_tool_call(content: Any) -> tuple[str, dict[str, Any]] | None:
    """(tool name, arguments) if the whole of ``content`` is a tool call written as text, else None.

    Only a reply that is nothing but the call counts: prose with some JSON in it is left alone."""
    if not isinstance(content, str):
        return None
    match = _TEXT_CALL.match(content)
    found = _TEXT_ARGS.search(content, match.end()) if match else None
    if match is None or found is None:
        return None
    try:
        args, end = json.JSONDecoder().raw_decode(content, found.end())
    except ValueError:  # the arguments are broken: still a (bad) call, and the tool will say so
        args, end = {}, found.end()
    rest = content[end:].strip().strip("`").strip()
    if rest.strip("} \n\t") or not content.rstrip().rstrip("`").rstrip().endswith("}"):
        return None
    return match.group("name"), args if isinstance(args, dict) else {}


class BabyAI:
    def __init__(self, config_path: str = "config.toml", voice_enabled: bool = False):
        self.config_path = os.path.abspath(os.path.expanduser(config_path))
        self.config = load_config(config_path)
        self.voice_enabled = voice_enabled or bool(self.config.get("voice", {}).get("enabled", False))

        memory_path = self.config.get("memory", {}).get("db_path", "~/.local/share/babyai/memory.db")
        self.memory = MemoryStore(memory_path)
        self.safety_gate = SafetyGate(level=self.config.get("safety", {}).get("level", "strict"))
        self.context = ConversationContext(self.config.get("agent", {}).get("system_prompt", DEFAULT_SYSTEM_PROMPT))
        # He can upgrade himself (change his code on a branch, update, switch model, keep notes), every change approved by the
        # user. [upgrade] enabled = false turns it off.
        self.upgrade = UpgradeContext(self, self.config, self.config_path) if self.config.get("upgrade", {}).get("enabled", True) else None
        # He learns the user's habits - counts only, on their computer, which they can see, pause and wipe. [habits] enabled = false
        # turns it off. What he learns goes into his prompt as a hint; the safety gate never reads it.
        habit_config = self.config.get("habits", {})
        habit_dir = habit_config.get("dir") or os.path.join(os.path.dirname(os.path.expanduser(memory_path)), "habits")
        self.habits = Habits(habit_dir) if habit_config.get("enabled", True) else None
        # How he sounds: four sliders the user can move, kept on their computer; like habits they are a hint for his prompt,
        # never a permission - the safety gate does not read them. [personality] enabled = false turns it off.
        personality_config = self.config.get("personality", {})
        personality_dir = personality_config.get("dir") or os.path.join(
            os.path.dirname(os.path.expanduser(memory_path)), "personality")
        self.personality = (
            Personality(personality_dir, defaults_from_config(personality_config))
            if personality_config.get("enabled", True) else None
        )
        self.tool_registry = build_default_registry(
            memory_store=self.memory, config=self.config, upgrade=self.upgrade, habits=self.habits,
            personality=self.personality,
        )
        # He thinks for himself about how to get better and suggests it - he only suggests (babyai/upgrade/ideas.py). [ideas] enabled = false turns it off.
        self.ideas = self.upgrade.ideas if self.upgrade is not None else None
        blocks = [
            block for block in (
                self.personality.prompt_block if self.personality is not None else None,
                self.upgrade.notes.prompt_block if self.upgrade is not None else None,
                self.ideas.prompt_block if self.ideas is not None else None,
                self.habits.prompt_block if self.habits is not None else None,
            ) if block
        ]
        if blocks:
            self.context.extra_system = lambda: "".join(block() for block in blocks)

        # Set by the front end (CLI prompt, desktop bubble). Without one, anything the
        # safety gate wants confirmed is declined rather than silently run.
        self.confirm: ConfirmHook | None = None
        # What to say when the model can't be reached, with {url} and {reason} filled in. Unset keeps the
        # plain canned text-mode replies; the desktop character sets it so she says what is wrong.
        self.offline_reply: str | None = None

        llm_cfg = self.config.get("llm", {})
        self.llm_client = LLMClient(
            base_url=llm_cfg.get("base_url", "http://localhost:11434/v1"),
            model=llm_cfg.get("model", "llama3.1:8b"),
            api_key=llm_cfg.get("api_key", ""),
            timeout=llm_cfg.get("timeout", 120),
        )
        self._tools_ok = True  # flips off if the model says it can't use tools

    def _noticed(self, what: str, *args: Any) -> None:
        """Tell the ideas store about something he noticed. Noticing must never get in the way of an answer."""
        if self.ideas is not None:
            try:
                getattr(self.ideas, what)(*args)
            except Exception:
                pass

    def _nudge(self) -> str:
        if self.ideas is None:
            return ""
        try:
            return self.ideas.nudge()
        except Exception:
            return ""

    def start_idea_checks(self) -> None:
        """Front ends call this once at start-up: look for a newer version in the background (a read-only fetch, at most once a day)."""
        if self.ideas is not None and self.upgrade is not None and self.ideas.check_due():
            threading.Thread(target=self.ideas.check_updates, args=(self.upgrade.version,), daemon=True, name="babyai-idea-check").start()

    def cli_confirm(self, name: str, arguments: dict[str, Any], verdict: dict[str, Any]) -> bool:
        shown = ", ".join(f"{key}={str(value)[:80]!r}" for key, value in arguments.items()) or "no arguments"
        risk = verdict.get("risk_level")
        print(f"BabyAI wants to run {name} ({getattr(risk, 'value', risk)}): {shown}")
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
        if self.habits is not None:
            try:
                self.habits.observe(user_input)
            except Exception:  # learning must never get in the way of an answer
                pass
        if not self._tools_ok:
            self._noticed("plain_turn")  # (counted first: an idea this makes can be raised in this very reply)
        self._noticed("begin_turn")  # at most one idea he may mention in this message
        self.context.add_user_message(user_input)
        messages = self.context.build_messages()
        tool_schemas = self.tool_registry.get_openai_tools() if self._tools_ok else None

        try:
            result = self._complete(messages, tool_schemas)
        except Exception as exc:
            if self.offline_reply:
                self.context.history.pop()  # as far as the model is concerned this turn never happened
                reason, hint = self._explain_failure(exc)
                url = getattr(self.llm_client, "base_url", "the model server")
                return self.offline_reply.format(url=url, reason=reason, hint=hint)
            fallback = self._fallback_response(user_input)
            self.context.add_assistant_message(fallback)
            return fallback

        self._noticed("mark_shown")  # the model got the message, so whatever idea was put in front of it counts as raised
        message = result.get("choices", [{}])[0].get("message", {})
        tool_calls = message.get("tool_calls") or []
        written = None if tool_calls or not self._tools_ok else text_tool_call(message.get("content"))
        if written:  # a call written as text: treat it as the call it is, and never show the JSON
            self._noticed("text_call")
            tool_calls = [{"id": "call_text", "function": {"name": written[0], "arguments": json.dumps(written[1])}}]
            message = {**message, "content": ""}

        if tool_calls:
            assistant_reply = self._run_rounds(messages, message, tool_calls)
            self.context.add_assistant_message(assistant_reply)
            return assistant_reply

        content = message.get("content") or "I’m here and ready to help."
        self.context.add_assistant_message(content)
        return content

    def _complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> dict[str, Any]:
        try:
            return self.llm_client.complete(messages=messages, tools=tools, stream=False)
        except Exception as exc:
            if tools and "support tools" in str(exc).lower():
                # Ollama refuses tool definitions for models that can't use them. Carry on as a plain chat.
                self._tools_ok = False
                return self.llm_client.complete(messages=messages, tools=None, stream=False)
            raise

    def _explain_failure(self, exc: Exception) -> tuple[str, str]:
        """(what went wrong, what to do about it) for a model request that failed, in plain words."""
        status = getattr(exc, "status", None)
        detail = str(getattr(exc, "detail", "") or "")
        model = getattr(self.llm_client, "model", "the model")
        text = str(exc).lower()
        if status == 404:
            lister = getattr(self.llm_client, "list_models", None)
            have = lister() if callable(lister) else []
            if have:
                return (f"there's no model called {model!r} there; it has {', '.join(have[:8])}",
                        "Set [llm] model in config.toml to one of those, then ask me again.")
            return (f"there's no model called {model!r} there",
                    f"Pull one (for Ollama: ollama pull {model}) or set [llm] model in config.toml, then ask me again.")
        if status in (401, 403):
            return "the server refused my key", "Check [llm] api_key in config.toml."
        if status:
            return f"the server answered {status}" + (f" ({detail})" if detail else ""), "Check [llm] model and base_url in config.toml."
        if "timed out" in text:
            return "it took too long to answer", "The model may still be loading: try again in a moment, or raise [llm] timeout in config.toml."
        reason = str(exc).replace("LLM request failed:", "").replace("urlopen error", "").strip(" <>")[:100]
        return reason or "no reason given", "Start a model server, like Ollama, or point [llm] base_url in config.toml at one, then ask me again."

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
            self._noticed("missing_tool", name)
            return f"There is no tool called {name!r}."
        verdict = self.safety_gate.check(name, args, interactive=True)
        if not verdict["allowed"]:
            if not verdict["needs_confirmation"]:
                return f"Blocked by the safety gate: {verdict['reason']}."
            approved = self._ask_confirmation(name, self._preview(name, args), verdict)
            self.safety_gate.record_confirmation(name, args, approved)
            if not approved:
                return f"The user declined to let {name} run."
        try:
            result = self.tool_registry.call(name=name, arguments=args, interactive=True)
        except TypeError as exc:  # the model passed arguments the tool does not take
            return f"{name} was called with bad arguments: {exc}"
        except Exception as exc:
            self._noticed("tool_crashed", name)
            return f"{name} failed: {exc}"
        if self.habits is not None:
            try:
                self.habits.observe_tool(name)
            except Exception:
                pass
        if isinstance(result, dict):
            text = str(result.get("content", ""))
        else:
            text = str(getattr(result, "content", result))
        return text[:MAX_TOOL_OUTPUT]

    def _preview(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """The arguments as the confirmation prompt shows them: the tool's own summary if it has one."""
        tool = self.tool_registry.get(name)
        try:
            shown = tool.preview(**args) if tool is not None else None
        except Exception:
            shown = None
        return shown if isinstance(shown, dict) and shown else args

    def _ask_confirmation(self, name: str, args: dict[str, Any], verdict: dict[str, Any]) -> bool:
        if self.confirm is None:
            return False
        try:
            return bool(self.confirm(name, args, verdict))
        except Exception:
            return False

    def _run_rounds(self, messages: list[dict[str, Any]], message: dict[str, Any], tool_calls: list[dict[str, Any]]) -> str:
        """Run the model's tool calls, hand the results back, and return what she says about them.

        Normally that is one round: the follow-up is plain talk. While he is working on himself (upgrade_* tools)
        the follow-up may call more tools - a change takes several steps - up to MAX_ROUNDS."""
        conversation, all_calls = list(messages), []
        for round_number in range(MAX_ROUNDS):
            calls = []
            for index, call in enumerate(tool_calls[:MAX_TOOL_CALLS]):
                if len(all_calls) + len(calls) >= MAX_TOOL_CALLS_TOTAL:
                    break
                name, args = self._parse_call(call)
                calls.append((call.get("id") or f"call_{round_number}_{index}", name, args, self._run_tool(name, args)))
            all_calls += calls
            nudge = self._nudge()  # an idea that formed while he worked on this message is raised in his next reply
            working = any(name.startswith("upgrade_") for _id, name, _args, _text in all_calls)
            more = working and self._tools_ok and round_number + 1 < MAX_ROUNDS and len(all_calls) < MAX_TOOL_CALLS_TOTAL
            conversation, reply = self._followup(conversation, message, calls, self.tool_registry.get_openai_tools() if more else None, nudge)
            if nudge and reply:
                self._noticed("mark_shown")  # the model has it now, so the idea counts as raised
            tool_calls = reply.get("tool_calls") or []
            written = None if tool_calls or not more else text_tool_call(reply.get("content"))
            if written:
                self._noticed("text_call")
                tool_calls = [{"id": "call_text", "function": {"name": written[0], "arguments": json.dumps(written[1])}}]
                reply = {**reply, "content": ""}
            if not (more and tool_calls):
                text = str(reply.get("content") or "").strip()
                if text_tool_call(text):  # still calling tools instead of talking
                    text = ""
                break
            message = reply
        else:
            text = ""
        # no follow-up from the model: show what the tools said
        return text or "\n".join(f"{name}: {result}" for _id, name, _args, result in all_calls)

    def _followup(self, conversation: list[dict[str, Any]], message: dict[str, Any], calls: list[tuple], tools: list[dict[str, Any]] | None, nudge: str = "") -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Hand the tool results back to the model. (the conversation so far including them, the model's reply message)

        ``nudge`` is an idea of his that has only just formed: it goes to the model with the tool results (never into what the user is shown)."""
        followup = list(conversation)
        followup.append({
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": [
                {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
                for call_id, name, args, _text in calls
            ],
        })
        for position, (call_id, _name, _args, text) in enumerate(calls):
            last = position == len(calls) - 1
            followup.append({"role": "tool", "tool_call_id": call_id, "content": f"{text}\n\n{nudge}" if nudge and last else text})
        try:
            result = self.llm_client.complete(messages=followup, tools=tools, stream=False)
            return followup, result.get("choices", [{}])[0].get("message", {})
        except Exception:
            return followup, {}

    def _fallback_response(self, user_input: str) -> str:
        lowered = user_input.lower()
        if "hello" in lowered or "hi" in lowered:
            return "Hello! I am BabyAI, running in text-only mode."
        if "remember" in lowered or "memory" in lowered:
            return "I can remember facts and search my local SQLite memory store."
        if "list" in lowered and "file" in lowered:
            return "I can list files with the list_directory tool when available."
        return "I am BabyAI and I am ready to help in text mode."
