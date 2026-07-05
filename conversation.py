"""Short-term dialogue memory passed to LLM-backed replies."""
from dataclasses import dataclass, field


@dataclass
class ConversationHistory:
    max_messages: int = 20
    _messages: list[dict[str, str]] = field(default_factory=list)

    def as_list(self) -> list[dict[str, str]]:
        return list(self._messages)

    def add_exchange(self, user: str, assistant: str) -> None:
        user = user.strip()
        assistant = assistant.strip()
        if not user or not assistant:
            return
        self._messages.append({"role": "user", "content": user})
        self._messages.append({"role": "assistant", "content": assistant})
        if len(self._messages) > self.max_messages:
            self._messages = self._messages[-self.max_messages :]

    def clear(self) -> None:
        self._messages.clear()
