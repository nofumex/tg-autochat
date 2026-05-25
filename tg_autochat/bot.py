from __future__ import annotations

import logging
from typing import Any

from .config import Settings, load_settings
from .llm import LLMChain, LLMClient, LLMConfig, LLMError
from .responder import IncomingMessage, Responder
from .storage import StateStore
from .telegram import TelegramAPIError, TelegramClient, backoff_sleep


ALLOWED_UPDATES = [
    "message",
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
]


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


class AutoChatBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.tg = TelegramClient(settings.bot_token)
        self.store = StateStore(settings.state_file)
        self.responder = self._build_responder(settings)
        self.bot_id: int | None = None

    def start(self) -> None:
        me = self.tg.get_me()
        self.bot_id = me["id"]
        logging.info(
            "Bot @%s is ready. can_connect_to_business=%s",
            me.get("username"),
            me.get("can_connect_to_business"),
        )
        self._set_commands()

        if self.settings.auto_delete_webhook:
            self.tg.delete_webhook(drop_pending_updates=False)
            logging.info("Webhook deleted for local long polling.")

        self._poll_forever()

    def _poll_forever(self) -> None:
        offset: int | None = None
        failures = 0

        while True:
            try:
                updates = self.tg.get_updates(
                    offset=offset,
                    timeout=self.settings.poll_timeout,
                    allowed_updates=ALLOWED_UPDATES,
                )
                failures = 0
            except TelegramAPIError as exc:
                failures += 1
                logging.warning("%s", exc)
                backoff_sleep(failures)
                continue

            for update in updates:
                update_id = update["update_id"]
                offset = update_id + 1
                if self.store.was_handled(update_id):
                    continue
                try:
                    self.handle_update(update)
                    self.store.remember_update(update_id)
                    self.store.save()
                except Exception:
                    logging.exception("Failed to handle update_id=%s", update_id)

    def handle_update(self, update: dict[str, Any]) -> None:
        if "business_connection" in update:
            self._handle_business_connection(update["business_connection"])
            return

        if "business_message" in update:
            self._handle_business_message(update["business_message"], edited=False)
            return

        if "edited_business_message" in update:
            self._handle_business_message(update["edited_business_message"], edited=True)
            return

        if "deleted_business_messages" in update:
            self._handle_deleted_business_messages(update["deleted_business_messages"])
            return

        if "message" in update:
            self._handle_direct_message(update["message"])

    def _handle_business_connection(self, connection: dict[str, Any]) -> None:
        self.store.upsert_connection(connection)
        user = connection.get("user", {})
        can_reply = self._right_enabled(connection, "can_reply", legacy_key="can_reply")
        can_read = self._right_enabled(connection, "can_read_messages")
        enabled = connection.get("is_enabled")

        logging.info(
            "Business connection %s from user_id=%s enabled=%s can_reply=%s",
            connection.get("id"),
            user.get("id"),
            enabled,
            can_reply,
        )
        self.store.add_event(
            {
                "type": "business_connection",
                "connection_id": connection.get("id"),
                "enabled": enabled,
                "user_id": user.get("id"),
                "can_reply": can_reply,
                "can_read_messages": can_read,
            }
        )

        warning = ""
        if enabled and not can_reply:
            warning = (
                "\n\nAuto-reply is OFF for this connection: grant the bot permission "
                "to reply to messages in Telegram connection settings."
            )
        self._notify_admin(
            "Business connection updated:\n"
            f"id: {connection.get('id')}\n"
            f"user: {user.get('id')} @{user.get('username', '')}\n"
            f"enabled: {enabled}\n"
            f"can_reply: {can_reply}\n"
            f"can_read_messages: {can_read}"
            f"{warning}"
        )

    def _handle_business_message(self, message: dict[str, Any], *, edited: bool) -> None:
        connection_id = message.get("business_connection_id")
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        text = message.get("text") or message.get("caption") or ""
        chat_id = chat.get("id")
        message_id = message.get("message_id")

        if not connection_id or not chat_id or not message_id:
            return

        connection = self._resolve_business_connection(connection_id)
        if not self._can_reply(connection):
            logging.info(
                "Skip message: connection is missing/disabled or has no can_reply right. connection_id=%s",
                connection_id,
            )
            return

        owner_id = (connection.get("user") or {}).get("id") if connection else None
        if owner_id and sender.get("id") == owner_id:
            logging.info("Skip owner-sent business message.")
            return

        if not text.strip():
            logging.info("Skip non-text business message in chat_id=%s.", chat_id)
            return

        self.store.add_event(
            {
                "type": "business_message",
                "connection_id": connection_id,
                "chat_id": chat_id,
                "message_id": message_id,
                "from_id": sender.get("id"),
                "edited": edited,
                "text": text[:500],
            }
        )
        self.store.add_chat_message(
            connection_id=connection_id,
            chat_id=chat_id,
            role="user",
            text=text,
        )

        incoming = IncomingMessage(
            text=text,
            sender_name=self._display_name(sender),
            chat_title=chat.get("title") or chat.get("username") or str(chat_id),
            history=self.store.get_chat_history(connection_id=connection_id, chat_id=chat_id),
        )

        try:
            if not self.responder:
                raise LLMError("No LLM responder configured")
            reply = self.responder.build_reply(incoming)
        except LLMError as exc:
            logging.warning("No LLM reply generated. Message will stay unread and unanswered: %s", exc)
            self.store.add_event(
                {
                    "type": "llm_error",
                    "connection_id": connection_id,
                    "chat_id": chat_id,
                    "error": str(exc)[:500],
                }
            )
            return

        try:
            self.tg.send_chat_action(
                business_connection_id=connection_id,
                chat_id=chat_id,
            )
        except TelegramAPIError as exc:
            logging.info("Chat action skipped after LLM success: %s", exc.description)

        self.tg.send_message(
            business_connection_id=connection_id,
            chat_id=chat_id,
            text=reply,
            reply_to_message_id=message_id,
        )
        self.store.add_chat_message(
            connection_id=connection_id,
            chat_id=chat_id,
            role="assistant",
            text=reply,
        )
        if self._right_enabled(connection, "can_read_messages"):
            try:
                self.tg.read_business_message(
                    business_connection_id=connection_id,
                    chat_id=chat_id,
                    message_id=message_id,
                )
            except TelegramAPIError as exc:
                logging.info("Read skipped after reply: %s", exc.description)
        logging.info("Replied to business chat_id=%s message_id=%s", chat_id, message_id)

    def _handle_deleted_business_messages(self, deleted: dict[str, Any]) -> None:
        self.store.add_event(
            {
                "type": "deleted_business_messages",
                "connection_id": deleted.get("business_connection_id"),
                "chat_id": (deleted.get("chat") or {}).get("id"),
                "message_ids": deleted.get("message_ids", []),
            }
        )
        logging.info(
            "Deleted business messages in chat_id=%s ids=%s",
            (deleted.get("chat") or {}).get("id"),
            deleted.get("message_ids"),
        )

    def _handle_direct_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        from_user = message.get("from") or {}
        chat_id = chat.get("id")
        text = (message.get("text") or "").strip()
        if not chat_id or not text:
            return

        if self.settings.admin_id and from_user.get("id") != self.settings.admin_id:
            return

        if text.startswith("/last"):
            self._send_last_events(chat_id)
            return

        self._send_status(chat_id)

    def _send_status(self, chat_id: int) -> None:
        me = self.tg.get_me()
        connections = self.store.active_connections()
        lines = [
            f"bot: @{me.get('username')}",
            f"can_connect_to_business: {me.get('can_connect_to_business')}",
            f"responder_mode: {self.settings.responder_mode}",
            f"free_llm_models: {', '.join(self.settings.free_llm_fallback_models) if self.settings.free_llm_api_key else '-'}",
            f"llm_chain: {', '.join(self.responder.generator.names) if self.responder else '-'}",
            f"system_prompt: {self.settings.llm_system_prompt_source}",
            f"system_prompt_chars: {len(self.settings.llm_system_prompt)}",
            f"active_connections: {len(connections)}",
        ]
        for connection in connections[:5]:
            user = connection.get("user") or {}
            lines.append(
                f"- {connection.get('id')}: user_id={user.get('id')} "
                f"can_reply={self._right_enabled(connection, 'can_reply', legacy_key='can_reply')} "
                f"can_read_messages={self._right_enabled(connection, 'can_read_messages')}"
            )
        self.tg.send_message(chat_id=chat_id, text="\n".join(lines))

    def _send_last_events(self, chat_id: int) -> None:
        events = self.store.data.get("recent_events", [])[-10:]
        if not events:
            self.tg.send_message(chat_id=chat_id, text="No events yet.")
            return

        lines = []
        for event in events:
            event_type = event.get("type")
            lines.append(
                f"{event.get('at')} {event_type} chat={event.get('chat_id')} conn={event.get('connection_id')}"
            )
        self.tg.send_message(chat_id=chat_id, text="\n".join(lines))

    def _notify_admin(self, text: str) -> None:
        if not self.settings.admin_id:
            return
        try:
            self.tg.send_message(chat_id=self.settings.admin_id, text=text, disable_notification=True)
        except TelegramAPIError as exc:
            logging.info("Admin notification skipped: %s", exc.description)

    def _resolve_business_connection(self, connection_id: str) -> dict[str, Any] | None:
        connection = self.store.get_connection(connection_id)
        if self._can_reply(connection):
            return connection

        try:
            fetched = self.tg.get_business_connection(connection_id)
        except TelegramAPIError as exc:
            logging.info(
                "Could not refresh business connection %s: %s",
                connection_id,
                exc.description,
            )
            return connection

        self.store.upsert_connection(fetched)
        can_reply = self._right_enabled(fetched, "can_reply", legacy_key="can_reply")
        can_read = self._right_enabled(fetched, "can_read_messages")
        logging.info(
            "Refreshed business connection %s enabled=%s can_reply=%s can_read_messages=%s",
            connection_id,
            fetched.get("is_enabled"),
            can_reply,
            can_read,
        )
        return fetched

    def _set_commands(self) -> None:
        try:
            self.tg.set_my_commands(
                [
                    {"command": "status", "description": "Show bot and connection status"},
                    {"command": "last", "description": "Show recent handled events"},
                ]
            )
        except TelegramAPIError as exc:
            logging.info("Command setup skipped: %s", exc.description)

    @staticmethod
    def _build_responder(settings: Settings) -> Responder | None:
        if settings.responder_mode not in {"llm", "ai"}:
            logging.warning("LLM responder is disabled. Business messages will not be answered.")
            return None

        clients: list[LLMClient] = []
        if settings.free_llm_api_key and settings.free_llm_base_url:
            for model in settings.free_llm_fallback_models:
                clients.append(
                    LLMClient(
                        LLMConfig(
                            name=f"free_llm:{model}",
                            api_key=settings.free_llm_api_key,
                            base_url=settings.free_llm_base_url,
                            endpoint=settings.free_llm_endpoint,
                            model=model,
                            timeout=settings.free_llm_timeout,
                            max_tokens=settings.free_llm_max_tokens,
                            system_prompt=settings.llm_system_prompt,
                            instruction_role="system",
                            max_tokens_field="max_tokens",
                            include_temperature=True,
                        )
                    )
                )

        if not clients:
            logging.warning("No LLM providers configured. Business messages will not be answered.")
            return None

        chain = LLMChain(clients)
        logging.info("LLM responder enabled. chain=%s", ", ".join(chain.names))
        return Responder(chain, settings.reply_prefix)

    @staticmethod
    def _can_reply(connection: dict[str, Any] | None) -> bool:
        if not connection or not connection.get("is_enabled"):
            return False
        return AutoChatBot._right_enabled(connection, "can_reply", legacy_key="can_reply")

    @staticmethod
    def _right_enabled(
        connection: dict[str, Any] | None,
        right_key: str,
        *,
        legacy_key: str | None = None,
    ) -> bool:
        if not connection:
            return False
        rights = connection.get("rights") or {}
        if rights.get(right_key) is True:
            return True
        if legacy_key and connection.get(legacy_key) is True:
            return True
        return False

    @staticmethod
    def _display_name(user: dict[str, Any]) -> str:
        parts = [user.get("first_name"), user.get("last_name")]
        name = " ".join(part for part in parts if part)
        return name or user.get("username") or ""


def run() -> None:
    setup_logging()
    settings = load_settings()
    AutoChatBot(settings).start()
