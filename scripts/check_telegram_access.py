from __future__ import annotations

import logging
import os
import sys

from aggregator.telegram import TelegramAPIError, TelegramClient


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    channel = os.getenv("TELEGRAM_CHANNEL", "").strip()
    if not token or not channel:
        logging.info("Проверка Telegram пропущена: токен или канал не настроены")
        return 0

    try:
        _, username = TelegramClient(token).get_identity()
        logging.info("Telegram token accepted: bot=@%s", username)
        access = TelegramClient(token).check_channel_access(channel)
    except TelegramAPIError as exc:
        logging.error(
            "Проверка Telegram не удалась: method=%s http_status=%s",
            exc.method,
            exc.status_code,
        )
        if exc.method == "getChatMember" and exc.status_code == 400:
            try:
                channels = TelegramClient(token).discover_channels()
            except Exception as discovery_exc:
                logging.error("Автообнаружение канала не удалось (%s)", type(discovery_exc).__name__)
            else:
                for discovered in channels:
                    logging.info(
                        "DISCOVERED_CHANNEL chat_id=%s title=%s username=%s",
                        discovered.chat_id,
                        discovered.title,
                        discovered.username or "(private)",
                    )
        return 1
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
