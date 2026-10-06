from __future__ import annotations

import logging
import os
import sys

from aggregator.telegram import TelegramClient


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    channel = os.getenv("TELEGRAM_CHANNEL", "").strip()
    if not token or not channel:
        logging.info("Проверка Telegram пропущена: токен или канал не настроены")
        return 0

    try:
        access = TelegramClient(token).check_channel_access(channel)
    except Exception as exc:
        logging.error(
            "Проверка Telegram не удалась (%s). Проверьте TELEGRAM_BOT_TOKEN в GitHub Secrets",
            type(exc).__name__,
        )
        return 1
    logging.info(
        "Telegram bot=@%s channel=%s status=%s can_post_messages=%s",
        access.username,
        channel,
        access.status,
        str(access.can_post_messages).lower(),
    )
    if not access.can_post_messages:
        logging.error("Бот не имеет права публиковать сообщения в канале")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
