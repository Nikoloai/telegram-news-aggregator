from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TelegramAccess:
    username: str
    status: str
    can_post_messages: bool


class TelegramClient:
    def __init__(self, token: str, timeout: int = 20):
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN не задан")
        self._base_url = f"https://api.telegram.org/bot{token}"
        self.timeout = timeout

    def _request(self, method: str, **params: object) -> dict[str, object]:
        response = requests.get(
            f"{self._base_url}/{method}",
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API отклонил метод {method}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"Telegram API вернул неожиданный ответ для {method}")
        return result

    def check_channel_access(self, chat_id: str) -> TelegramAccess:
        bot = self._request("getMe")
        member = self._request("getChatMember", chat_id=chat_id, user_id=bot["id"])
        status = str(member.get("status", "unknown"))
        can_post = status == "creator" or (
            status == "administrator" and bool(member.get("can_post_messages"))
        )
        return TelegramAccess(
            username=str(bot.get("username", "")),
            status=status,
            can_post_messages=can_post,
        )

    def send(self, chat_id: str, text: str) -> int:
        response = requests.post(
            f"{self._base_url}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError("Telegram API отклонил сообщение")
        message_id = int(payload["result"]["message_id"])
        LOGGER.info("Telegram принял сообщение id=%s", message_id)
        return message_id
