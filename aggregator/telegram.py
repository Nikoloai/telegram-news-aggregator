from __future__ import annotations

import logging
import json
from html import unescape
from bs4 import BeautifulSoup
from dataclasses import dataclass

import requests

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TelegramAccess:
    username: str
    status: str
    can_post_messages: bool


@dataclass(frozen=True, slots=True)
class TelegramChannel:
    chat_id: int
    title: str
    username: str


class TelegramAPIError(RuntimeError):
    def __init__(self, method: str, status_code: int):
        super().__init__(f"Telegram API {method} вернул HTTP {status_code}")
        self.method = method
        self.status_code = status_code


class TelegramClient:
    def __init__(self, token: str, timeout: int = 20):
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN не задан")
        self._base_url = f"https://api.telegram.org/bot{token}"
        self.timeout = timeout

    def _request_result(self, method: str, **params: object) -> object:
        response = requests.get(
            f"{self._base_url}/{method}",
            params=params,
            timeout=self.timeout,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise TelegramAPIError(method, response.status_code) from exc
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API отклонил метод {method}")
        return payload.get("result")

    def _request(self, method: str, **params: object) -> dict[str, object]:
        result = self._request_result(method, **params)
        if not isinstance(result, dict):
            raise RuntimeError(f"Telegram API вернул неожиданный ответ для {method}")
        return result

    def discover_channels(self) -> list[TelegramChannel]:
        result = self._request_result(
            "getUpdates",
            limit=100,
            timeout=0,
            allowed_updates=json.dumps(["my_chat_member"]),
        )
        if not isinstance(result, list):
            raise RuntimeError("Telegram API вернул неожиданный ответ для getUpdates")

        channels: dict[int, TelegramChannel] = {}
        for update in result:
            if not isinstance(update, dict):
                continue
            membership = update.get("my_chat_member")
            if not isinstance(membership, dict):
                continue
            chat = membership.get("chat")
            if not isinstance(chat, dict) or chat.get("type") != "channel":
                continue
            chat_id = chat.get("id")
            if not isinstance(chat_id, int):
                continue
            channels[chat_id] = TelegramChannel(
                chat_id=chat_id,
                title=str(chat.get("title", "")),
                username=str(chat.get("username", "")),
            )
        return list(channels.values())

    def get_identity(self) -> tuple[int, str]:
        bot = self._request("getMe")
        return int(bot["id"]), str(bot.get("username", ""))

    def get_updates(self, offset: int = 0) -> list[dict[str, object]]:
        result = self._request_result(
            "getUpdates", offset=offset, limit=100, timeout=0,
            allowed_updates=json.dumps(["message", "my_chat_member"]),
        )
        if not isinstance(result, list):
            raise RuntimeError("Telegram API вернул неожиданный ответ для getUpdates")
        return [update for update in result if isinstance(update, dict)]

    def check_channel_access(self, chat_id: str) -> TelegramAccess:
        bot_id, username = self.get_identity()
        member = self._request("getChatMember", chat_id=chat_id, user_id=bot_id)
        status = str(member.get("status", "unknown"))
        can_post = status == "creator" or (
            status == "administrator" and bool(member.get("can_post_messages"))
        )
        return TelegramAccess(
            username=username,
            status=status,
            can_post_messages=can_post,
        )

    def send(self, chat_id: str, text: str, preview_url: str | None = None,
             image_url: str | None = None) -> int:
        # Telegram photo captions are limited to 1024 visible characters.
        # Long analytical posts remain single, complete text messages.
        caption_length = len(unescape(BeautifulSoup(text, 'html.parser').get_text()))
        if image_url and caption_length <= 1024:
            photo_response = requests.post(
                f"{self._base_url}/sendPhoto",
                json={
                    "chat_id": chat_id,
                    "photo": image_url,
                    "caption": text,
                    "parse_mode": "HTML",
                    "show_caption_above_media": True,
                },
                timeout=self.timeout,
            )
            if photo_response.status_code == 200:
                photo_payload = photo_response.json()
                if photo_payload.get("ok"):
                    message_id = int(photo_payload["result"]["message_id"])
                    LOGGER.info("Telegram принял пост с фото id=%s", message_id)
                    return message_id
                # A non-OK success HTTP status is ambiguous: never send another post.
                raise RuntimeError("Telegram не подтвердил отправку фотографии")
            if photo_response.status_code != 400:
                photo_response.raise_for_status()
                raise RuntimeError("Неопределённый ответ Telegram при отправке фотографии")
            # Explicit 400 is definitive rejection, not an ambiguous timeout.
            LOGGER.info("Фото отклонено Telegram (HTTP 400), отправляем текст с предпросмотром")
        preview_options: dict[str, object] = {"is_disabled": False}
        if preview_url:
            preview_options["url"] = preview_url
        response = requests.post(
            f"{self._base_url}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "link_preview_options": preview_options,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError("Telegram API отклонил сообщение")
        message_id = int(payload["result"]["message_id"])
        LOGGER.info("Telegram принял сообщение id=%s", message_id)
        return message_id
