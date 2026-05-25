from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {
            "connections": {},
            "handled_update_ids": [],
            "recent_events": [],
            "chat_history": {},
        }
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            self.data.update(loaded)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def remember_update(self, update_id: int) -> None:
        ids = self.data.setdefault("handled_update_ids", [])
        ids.append(update_id)
        del ids[:-200]

    def was_handled(self, update_id: int) -> bool:
        return update_id in set(self.data.get("handled_update_ids", []))

    def upsert_connection(self, connection: dict[str, Any]) -> None:
        connection_id = connection["id"]
        existing = self.data.setdefault("connections", {}).get(connection_id, {})
        existing.update(connection)
        existing["updated_at"] = utc_now()
        self.data["connections"][connection_id] = existing

    def get_connection(self, connection_id: str) -> dict[str, Any] | None:
        return self.data.get("connections", {}).get(connection_id)

    def active_connections(self) -> list[dict[str, Any]]:
        return [
            value
            for value in self.data.get("connections", {}).values()
            if value.get("is_enabled")
        ]

    def add_event(self, event: dict[str, Any]) -> None:
        events = self.data.setdefault("recent_events", [])
        events.append({"at": utc_now(), **event})
        del events[:-100]

    def add_chat_message(
        self,
        *,
        connection_id: str,
        chat_id: int,
        role: str,
        text: str,
    ) -> None:
        key = self._chat_key(connection_id, chat_id)
        history = self.data.setdefault("chat_history", {}).setdefault(key, [])
        history.append(
            {
                "at": utc_now(),
                "role": role,
                "text": text[:2000],
            }
        )
        del history[:-20]

    def get_chat_history(
        self,
        *,
        connection_id: str,
        chat_id: int,
        limit: int = 12,
    ) -> list[dict[str, str]]:
        key = self._chat_key(connection_id, chat_id)
        history = self.data.get("chat_history", {}).get(key, [])
        return history[-limit:]

    @staticmethod
    def _chat_key(connection_id: str, chat_id: int) -> str:
        return f"{connection_id}:{chat_id}"
