"""One-time publication, approved by channel owner. Image from the owner's uploaded screenshot.
Source: https://vot-tak.tv/96544562/putin-mesto-obshee-foto-si-trump
One-shot send intent persisted before Telegram API call to avoid duplicates.
"""
import base64
import html
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
import requests
from aggregator.storage import Storage
from aggregator.models import FeedItem
from aggregator.telegram import TelegramClient
from scripts.railway_cron import check_state

CHANNEL = "@GVOZDIchKAAA"
INTENT_KEY = "special-post:putin-apec-photo-20261118-v1"
TITLE = "Путину показали его место. Буквально"
SOURCE_URL = "https://vot-tak.tv/96544562/putin-mesto-obshee-foto-si-trump"
TEXT = """🇨🇳 <b>ПУТИНУ ПОКАЗАЛИ ЕГО МЕСТО. БУКВАЛЬНО.</b>

Двадцать пять лет человек добивался уважения. Грозил миру ядерным оружием, требовал разговаривать с ним на равных, рассуждал о новой архитектуре международной безопасности.

И вот наконец — Китай. Си Цзиньпин, Дональд Трамп, камеры, историческая фотография. Почти Ялта. Осталось только встать рядом с великими и принять соответствующее выражение лица.

Но тут выяснилось, что у китайского руководства собственные представления о величии российского президента.

Си жестом указал, куда Путину следует пройти. Трамп остался рядом с хозяином мероприятия. А Владимиру Владимировичу досталась роль человека, которому на свадьбе объяснили, что за столом молодожёнов сидят только родственники.

И ведь не возразишь. Не объявишь специальную военную операцию по освобождению места возле Си Цзиньпина.

Особенно обидно, наверное, после четверти века разговоров о том, что Россия снова встала с колен.

<b>Встать-то встала. Теперь главное — дождаться, пока товарищ Си разрешит, где именно.</b>"""
CAPTION = TEXT.replace("Двадцать пять лет человек добивался уважения. ", "Четверть века добивался уважения. ").replace("Но тут выяснилось, что у китайского руководства собственные представления о величии российского президента.", "Но у китайского руководства свои представления о величии.").replace("Особенно обидно, наверное, после четверти века разговоров о том, что Россия снова встала с колен.", "А ведь сколько было разговоров, что Россия встала с колен.")
CAPTION += '\n\n<a href="' + SOURCE_URL + '">Источник: «Вот Так», 18.11.2026</a>'
# Compact owner-provided screenshot, JPEG resized to 400 x 308. This is a real frame, not generated imagery.
PHOTO_B64 = """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"""
def main():
    logging.basicConfig(level=logging.INFO)
    path = Path(os.getenv("STATE_DB", "/data/state.db"))
    if path.resolve() != Path("/data/state.db"):
        raise RuntimeError("Wrong state db")
    check_state(path)
    if os.getenv("TELEGRAM_CHANNEL") != CHANNEL:
        raise RuntimeError("Wrong channel")
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    storage = Storage(path)
    try:
        existing = storage.get_meta(INTENT_KEY)
        if existing:
            print("Already processed: " + existing)
            return 0
        bot = TelegramClient(token)
        access = bot.check_channel_access(CHANNEL)
        if access.username != "tnews_aggregator_bot" or not access.can_post_messages:
            raise RuntimeError("Wrong bot credentials or insufficient access")
        if len(CAPTION.replace("<b>", "").replace("</b>", "").replace('<a href="' + SOURCE_URL + '">', '').replace("</a>", "")) > 1024:
            raise RuntimeError("Photo caption exceeds Telegram limit")
        img = base64.b64decode(PHOTO_B64, validate=True)
        if len(img) < 3000 or not img.startswith(b"\xff\xd8\xff"):
            raise RuntimeError("Bad owner image")
        now = datetime.now(timezone.utc)
        storage.set_meta(INTENT_KEY, json.dumps({"status": "sending", "when": now.isoformat()}))
        response = requests.post("https://api.telegram.org/bot" + token + "/sendPhoto",
            data={"chat_id": CHANNEL, "caption": CAPTION, "parse_mode": "HTML", "show_caption_above_media": "true"},
            files={"photo": ("putin-at-apec.jpg", img, "image/jpeg")}, timeout=35)
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError("Telegram sendPhoto returned not ok: " + str(result.get("description", "")))
        mid = int(result["result"]["message_id"])
        storage.set_meta(INTENT_KEY, json.dumps({"status": "published", "message_id": mid}))
        storage.save(FeedItem(source="Вот Так", title=TITLE, url=SOURCE_URL, published_at=now),
            "PUBLISHED", TEXT, "SATIRICAL", ["international", "politics"],
            telegram_message_id=mid, post_html=CAPTION, rubric="news")
        print(f"Published https://t.me/GVOZDIchKAAA/{mid}")
        return 0
    finally:
        storage.close()
if __name__ == "__main__":
    raise SystemExit(main())
