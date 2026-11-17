"""One-off approved editorial post about Kaliningrad's 2027 reserve-fund allocation.

Run from Railway with the existing migrated /data/state.db. The send-intent
is persisted before contacting Telegram: uncertain delivery can never cause a
duplicate retry. Once published this script is not used by the periodic job.
"""
from __future__ import annotations

import html
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from aggregator.corroboration import stories_match
from aggregator.dedupe import normalize_title, normalize_url
from aggregator.models import FeedItem
from aggregator.storage import Storage
from aggregator.telegram import TelegramClient
from scripts.railway_cron import check_state

LOG = logging.getLogger("special_budget_post")
SOURCE_URL = "https://ruwest.ru/news/162132/"
PHOTO_URL = ("https://commons.wikimedia.org/wiki/Special:FilePath/"
             "Alexey_Besprozvannykh_official_portrait%2C_2024_%28cropped%29.jpg?width=700")
INTENT_KEY = "special-post:kaliningrad-reserve-2027-20261117"
TITLE = "Беспрозванных выделил 1,3 млрд рублей из резервного фонда на выплаты в 2027 году"
CHANNEL = "@GVOZDIchKAAA"
BODY = (
    "💰 <b>В Калининграде на 2027 год приготовили ещё 1,3 млрд рублей. Для войны.</b>\n\n"
    "Губернатор Алексей Беспрозванных подписал распоряжение: из резервного фонда области "
    "выделяют 1,3 млрд рублей на единовременные выплаты участникам войны против Украины, "
    "членам их семей и военнослужащим пограничных органов ФСБ. Документ вступает в силу "
    "1 января 2027 года.\n\n"
    "Почти пять лет назад войну представляли как операцию с целями. "
    "Теперь у неё есть ежегодное бюджетное продолжение и собственное место в резервном фонде. "
    "Резервный фонд, конечно, на случай непредвиденных обстоятельств. "
    "Но война давно стала статьёй, которую власти предусмотрительно переносят в следующий год.\n\n"
    "Выплаты людям — обязательство государства. А вот решения, сделавшие эти расходы "
    "постоянными, принимают совсем другие люди.\n\n"
    "Источник: <a href=\"https://ruwest.ru/news/162132/\">Русский Запад, 17.11.2026</a>\n"
    "Фото: Gov39.ru, <a href=\"https://creativecommons.org/licenses/by/4.0/\">CC BY 4.0</a>."
)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    path = Path(os.environ.get("STATE_DB", "/data/state.db"))
    if path.resolve() != Path("/data/state.db"):
        LOG.error("Unexpected state location")
        return 1
    check_state(path)
    if os.environ.get("TELEGRAM_CHANNEL") != CHANNEL:
        LOG.error("Unexpected destination channel")
        return 1
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        LOG.error("Missing Telegram token")
        return 1
    storage = Storage(path)
    try:
        existing = storage.get_meta(INTENT_KEY)
        if existing is not None:
            LOG.info("Already handled or uncertain delivery; not sending again: %s", existing)
            return 0
        norm_url, norm_title = normalize_url(SOURCE_URL), normalize_title(TITLE)
        if storage.find_exact(norm_url, norm_title):
            LOG.info("This story is already recorded in the publication history")
            return 0
        now = datetime.now(timezone.utc)
        for row in storage.recent_published(limit=90):
            if stories_match(TITLE, str(row["title"]), threshold=77):
                LOG.info("Closely matching story already in recent publications")
                return 0
        last = storage.last_publication_at()
        if last and now - last < timedelta(hours=1):
            LOG.info("Hour spacing rule: last publication at %s; no special send", last.isoformat())
            return 0
        client = TelegramClient(token)
        access = client.check_channel_access(CHANNEL)
        if access.username != "tnews_aggregator_bot" or not access.can_post_messages:
            raise RuntimeError("Unrecognized Telegram bot or cannot publish")
        photo = requests.get(PHOTO_URL, timeout=(10, 30), headers={"User-Agent": "GvozdikaNews/1.0"})
        photo.raise_for_status()
        data = photo.content
        if not (data[:3] == bytes((255, 216, 255)) and 2000 < len(data) <= 9_000_000):
            raise RuntimeError("Photo fetch returned invalid JPEG")
        LOG.info("Verified original photo bytes=%d", len(data))
        # Persist before external call. On timeout/manual error NEVER resend blindly.
        storage.set_meta(INTENT_KEY, json.dumps({"status": "sending", "timestamp": now.isoformat()}))
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={
                "chat_id": CHANNEL,
                "caption": BODY,
                "parse_mode": "HTML",
                "show_caption_above_media": "true",
            },
            files={"photo": ("besprozvannykh-portrait.jpg", data, "image/jpeg")},
            timeout=35,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok") or not isinstance(payload.get("result"), dict):
            raise RuntimeError("Telegram did not confirm sendPhoto")
        message_id = int(payload["result"]["message_id"])
        storage.set_meta(INTENT_KEY, json.dumps({"status": "published", "message_id": message_id}))
        storage.save(
            FeedItem(source="Русский Запад", title=TITLE, url=SOURCE_URL, published_at=now),
            "PUBLISHED", BODY, "SATIRICAL", ["kaliningrad", "state_policy"],
            telegram_message_id=message_id, post_html=BODY, rubric="news",
        )
        LOG.info("Published photo post message_id=%d url=https://t.me/GVOZDIchKAAA/%d",
                 message_id, message_id)
        return 0
    except Exception as error:
        LOG.error("Safe stop (%s): external delivery may be uncertain; do not retry automatically",
                  type(error).__name__)
        return 1
    finally:
        storage.close()


if __name__ == "__main__":
    sys.exit(main())
