from __future__ import annotations

import logging

import requests

LOGGER = logging.getLogger(__name__)


class TelegramClient:
    def __init__(self, token: str, timeout: int = 20):
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN не задан")
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.timeout = timeout

    def send(self, chat_id: str, text: str) -> int:
        response = requests.post(
            self._url,
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
