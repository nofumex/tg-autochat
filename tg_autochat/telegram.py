from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


class TelegramAPIError(RuntimeError):
    def __init__(self, method: str, description: str, error_code: int | None = None):
        self.method = method
        self.description = description
        self.error_code = error_code
        super().__init__(f"{method} failed: {description}")


class TelegramClient:
    def __init__(self, token: str) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"

    def call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        payload = payload or {}
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/{method}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=payload.get("timeout", 30) + 10) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                raise TelegramAPIError(method, raw, exc.code) from exc
            raise TelegramAPIError(
                method,
                body.get("description", raw),
                body.get("error_code", exc.code),
            ) from exc
        except urllib.error.URLError as exc:
            raise TelegramAPIError(method, str(exc.reason)) from exc

        body = json.loads(raw)
        if not body.get("ok"):
            raise TelegramAPIError(
                method,
                body.get("description", raw),
                body.get("error_code"),
            )
        return body.get("result")

    def get_me(self) -> dict[str, Any]:
        return self.call("getMe")

    def delete_webhook(self, drop_pending_updates: bool = False) -> bool:
        return bool(self.call("deleteWebhook", {"drop_pending_updates": drop_pending_updates}))

    def get_updates(
        self,
        *,
        offset: int | None,
        timeout: int,
        allowed_updates: list[str],
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": allowed_updates,
        }
        if offset is not None:
            payload["offset"] = offset
        return self.call("getUpdates", payload)

    def send_message(
        self,
        *,
        chat_id: int | str,
        text: str,
        business_connection_id: str | None = None,
        reply_to_message_id: int | None = None,
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:4096],
            "disable_notification": disable_notification,
        }
        if business_connection_id:
            payload["business_connection_id"] = business_connection_id
        if reply_to_message_id:
            payload["reply_parameters"] = {"message_id": reply_to_message_id}
        return self.call("sendMessage", payload)

    def send_chat_action(
        self,
        *,
        chat_id: int | str,
        business_connection_id: str,
        action: str = "typing",
    ) -> bool:
        return bool(
            self.call(
                "sendChatAction",
                {
                    "business_connection_id": business_connection_id,
                    "chat_id": chat_id,
                    "action": action,
                },
            )
        )

    def read_business_message(
        self,
        *,
        business_connection_id: str,
        chat_id: int,
        message_id: int,
    ) -> bool:
        return bool(
            self.call(
                "readBusinessMessage",
                {
                    "business_connection_id": business_connection_id,
                    "chat_id": chat_id,
                    "message_id": message_id,
                },
            )
        )

    def set_my_commands(self, commands: list[dict[str, str]]) -> bool:
        return bool(self.call("setMyCommands", {"commands": commands}))


def backoff_sleep(attempt: int) -> None:
    time.sleep(min(30, 2**min(attempt, 5)))
