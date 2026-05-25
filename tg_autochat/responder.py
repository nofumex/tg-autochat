from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class TextGenerator(Protocol):
    def complete(
        self,
        *,
        text: str,
        sender_name: str,
        chat_title: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        ...


@dataclass(frozen=True)
class IncomingMessage:
    text: str
    sender_name: str
    chat_title: str
    history: list[dict[str, str]]


class Responder:
    def __init__(self, generator: TextGenerator, prefix: str = "") -> None:
        self.generator = generator
        self.prefix = prefix

    def build_reply(self, incoming: IncomingMessage) -> str:
        reply = self.generator.complete(
            text=incoming.text.strip(),
            sender_name=incoming.sender_name,
            chat_title=incoming.chat_title,
            history=incoming.history,
        )
        if self.prefix:
            return f"{self.prefix} {reply}".strip()
        return reply
