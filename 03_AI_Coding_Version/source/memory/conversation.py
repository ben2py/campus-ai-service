"""限长、可清空的会话记忆。"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Message:
    role: str
    content: str
    topic: str | None = None


class ConversationMemory:
    def __init__(self, max_turns: int = 6):
        if max_turns < 1:
            raise ValueError("max_turns 必须大于 0。")
        self.max_messages = max_turns * 2
        self._sessions: dict[str, deque[Message]] = defaultdict(
            lambda: deque(maxlen=self.max_messages)
        )

    def add(self, session_id: str, message: Message) -> None:
        if message.role not in {"user", "assistant", "tool"}:
            raise ValueError("不支持的消息角色。")
        self._sessions[session_id].append(message)

    def recent(self, session_id: str) -> list[Message]:
        return list(self._sessions[session_id])

    def last_topic(self, session_id: str) -> str | None:
        for message in reversed(self._sessions[session_id]):
            if message.topic:
                return message.topic
        return None

    def resolve_reference(self, session_id: str, question: str) -> str:
        if any(token in question for token in ("它", "那里", "该项", "这个", "这项", "还有呢")):
            topic = self.last_topic(session_id)
            if topic:
                return f"{topic}；{question}"
        return question

    def clear(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
