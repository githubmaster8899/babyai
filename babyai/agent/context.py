class ConversationContext:
    def __init__(self, system_prompt: str = DEFAULT_SYSTEM_PROMPT):
        self.system_prompt = system_prompt
        self.history: list[dict[str, str]] = []

    def add_user_message(self, content: str) -> None:
        self.history.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str) -> None:
        self.history.append({"role": "assistant", "content": content})

    def build_messages(self) -> list[dict[str, str]]:
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(self.history)
        return messages

    def reset(self) -> None:
        self.history.clear()


DEFAULT_SYSTEM_PROMPT = (
    "You are BabyAI, a local-first, privacy-focused AI assistant. "
    "Answer concisely and safely, prefer text-based help, and use tools when needed."
)
