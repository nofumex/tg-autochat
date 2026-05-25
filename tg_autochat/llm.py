from __future__ import annotations

import json
import logging
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMConfig:
    name: str
    api_key: str
    base_url: str
    endpoint: str
    model: str
    timeout: int
    max_tokens: int
    system_prompt: str
    instruction_role: str = "system"
    max_tokens_field: str = "max_tokens"
    include_temperature: bool = True


class LLMClient:
    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.url = self._build_url(config.base_url, config.endpoint)

    def complete(
        self,
        *,
        text: str,
        sender_name: str,
        chat_title: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": self.config.instruction_role, "content": self.config.system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"Собеседник: {sender_name or 'неизвестно'}\n"
                        f"Чат: {chat_title}\n\n"
                        f"{self._format_history(history)}"
                        f"Текущее сообщение: {text}\n\n"
                        "Сгенерируй один ответ для отправки в Telegram. "
                        "Не добавляй подписи, служебные комментарии и варианты ответа."
                    ),
                },
            ],
            self.config.max_tokens_field: self.config.max_tokens,
        }
        if self.config.include_temperature:
            payload["temperature"] = 0.6

        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise LLMError(f"{self.config.name} HTTP {exc.code}: {raw[:500]}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"{self.config.name} network error: {exc.reason}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise LLMError(f"{self.config.name} request timed out") from exc
        except json.JSONDecodeError as exc:
            raise LLMError(f"{self.config.name} returned invalid JSON") from exc

        content = self._clean_content(self._extract_content(body))
        if not content:
            raise LLMError(f"{self._label(body)} returned an empty response")
        return content.strip()

    def _label(self, body: dict[str, Any] | None = None) -> str:
        label = f"{self.config.name} model={self.config.model}"
        if body:
            routed = body.get("_routed_via") or {}
            actual_model = routed.get("model") or body.get("model")
            if actual_model and actual_model != self.config.model:
                label += f" routed={actual_model}"
            choices = body.get("choices") or []
            if choices:
                finish_reason = (choices[0] or {}).get("finish_reason")
                if finish_reason:
                    label += f" finish={finish_reason}"
        return label

    @staticmethod
    def _format_history(history: list[dict[str, str]] | None) -> str:
        if not history:
            return "История диалога: пока нет.\n\n"

        lines = ["История диалога:"]
        for item in history[-12:]:
            role = item.get("role", "")
            text = (item.get("text") or "").strip()
            if not text:
                continue
            label = "Бот" if role == "assistant" else "Агент"
            lines.append(f"{label}: {text}")
        return "\n".join(lines) + "\n\n"

    @staticmethod
    def _build_url(base_url: str, endpoint: str) -> str:
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint

        base = base_url.rstrip("/")
        path = "/" + endpoint.lstrip("/")
        if base.endswith("/v1") and path.startswith("/v1/"):
            path = path[3:]
        return base + path

    @staticmethod
    def _extract_content(body: dict[str, Any]) -> str:
        choices = body.get("choices") or []
        if not choices:
            return ""
        first = choices[0] or {}
        message = first.get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        text = first.get("text")
        if isinstance(text, str):
            return text
        return ""

    @staticmethod
    def _clean_content(content: str) -> str:
        content = content.strip()
        content = re.sub(r"(?is)<think>.*?</think>", "", content).strip()
        if content.lower().startswith("<think>"):
            return ""
        return content


class LLMChain:
    def __init__(self, clients: list[LLMClient]) -> None:
        self.clients = clients

    @property
    def names(self) -> list[str]:
        return [client.config.name for client in self.clients]

    def complete(
        self,
        *,
        text: str,
        sender_name: str,
        chat_title: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        if not self.clients:
            raise LLMError("No LLM providers configured")

        errors: list[str] = []
        for client in self.clients:
            try:
                return client.complete(
                    text=text,
                    sender_name=sender_name,
                    chat_title=chat_title,
                    history=history,
                )
            except LLMError as exc:
                logging.info("LLM provider failed, trying next if available: %s", exc)
                errors.append(str(exc))

        raise LLMError("All LLM providers failed: " + " | ".join(errors))
