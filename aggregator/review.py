from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from .models import FeedItem
from .storage import Storage
from .telegram import TelegramClient
from .readers import handle_reader_command
from .features import FEATURE_RUBRICS, valid_cost_body
from .satire import SATIRE_RUBRICS, satire_week_window
from .validator import FactPreservationValidator

LOGGER = logging.getLogger(__name__)


def review_item(row: object) -> FeedItem:
    data = json.loads(row["item_json"])
    published = data.pop("published_at", None)
    return FeedItem(**data, published_at=datetime.fromisoformat(published) if published else None)


def notify_pending(storage: Storage, telegram: TelegramClient, editor_chat_id: str) -> None:
    if not editor_chat_id.isdecimal() or int(editor_chat_id) <= 0:
        return
    for row in storage.pending_reviews(limit=3):
        reasons = "; ".join(json.loads(row["reasons"]))[:500]
        message = (
            f"🔎 <b>Пост №{row['id']} на проверку</b>\n"
            f"{html.escape(reasons)}\n\n{row['post_html']}\n\n"
            f"Опубликовать: <code>/publish {row['id']}</code>\n"
            f"Отклонить: <code>/skip {row['id']}</code>"
        )
        try:
            message_id = telegram.send(editor_chat_id, message)
        except Exception as exc:
            LOGGER.warning("Отправка редактору не удалась: %s", type(exc).__name__)
            break
        storage.mark_review_notified(int(row["id"]), message_id)


def process_editor_messages(
    storage: Storage, telegram: TelegramClient, editor_chat_id: str, channel: str,
    day_start: datetime, day_end: datetime, daily_limit: int, allow_publish: bool,
    sources: list[dict[str, object]] | None = None, privacy_key: str = "",
) -> int:
    """Only the configured private chat and its matching sender may approve posts."""
    sent = 0
    week_start, week_end = satire_week_window(datetime.now(timezone.utc))
    offset = int(storage.get_meta("telegram_update_offset") or "0")
    for update in telegram.get_updates(offset):
        update_id = update.get("update_id")
        if not isinstance(update_id, int):
            continue
        message = update.get("message")
        if not isinstance(message, dict):
            storage.set_meta("telegram_update_offset", str(update_id + 1))
            continue
        chat = message.get("chat", {})
        sender = message.get("from", {})
        text = str(message.get("text", "")).strip()
        private = isinstance(chat, dict) and isinstance(sender, dict) and chat.get("type") == "private"
        own_chat = private and chat.get("id") == sender.get("id") and not sender.get("is_bot")
        if own_chat and text.split(" ", 1)[0].split("@", 1)[0] == "/id":
            telegram.send(str(chat["id"]), f"Твой ID для EDITOR_CHAT_ID: <code>{chat['id']}</code>")
        elif own_chat:
            reply = handle_reader_command(storage, text, int(chat['id']), sources or [], privacy_key)
            if reply:
                telegram.send(str(chat['id']), reply)
                storage.set_meta("telegram_update_offset", str(update_id + 1))
                continue
        if own_chat and str(chat.get("id")) == editor_chat_id:
            if text == "/requests":
                requests = storage.pending_reader_requests(limit=10)
                reply = "Запросы читателей:\n" + "\n".join(
                    f"№{row['id']} · {row['votes']} запросов · "
                    + (f'<a href="{html.escape(row["public_url"], quote=True)}">{html.escape(row["source"])}</a>'
                       if row['public_url'] else html.escape(row['source']) + ' — ждём материал в RSS')
                    for row in requests
                ) if requests else "Запросов пока нет."
                telegram.send(editor_chat_id, reply)
            command = re.fullmatch(r"/(publish|skip)(?:@\w+)?\s+(\d+)", text)
            if command:
                action, review_id = command.group(1), int(command.group(2))
                row = storage.get_review(review_id)
                response = "Пост уже обработан или не найден."
                if row and row["status"] == "PENDING":
                    item = review_item(row)
                    if action == "skip":
                        storage.update_review(review_id, "REJECTED")
                        storage.save(item, "REJECTED", row["source_text"], row["mode"], str(row["topics"]).split(","))
                        response = "Пост отклонён."
                    elif not allow_publish:
                        response = "Автопубликация выключена. Пост остаётся на проверке."
                    elif daily_limit and storage.count_published_between(day_start, day_end) >= daily_limit:
                        response = "Дневной лимит достигнут. Пост остаётся на проверке."
                    elif row["rubric"] == "quote" and storage.rubric_count_between("quote", day_start, day_end):
                        response = "Цитата дня уже опубликована. Пост остаётся на проверке."
                    elif row['rubric'] in FEATURE_RUBRICS and storage.feature_count_between(day_start, day_end):
                        response = 'Авторская рубрика сегодня уже опубликована. Пост остаётся на проверке.'
                    elif row['rubric'] in SATIRE_RUBRICS and storage.satire_count_between(week_start, week_end) >= 2:
                        response = 'Две сатирические рубрики на этой неделе уже опубликованы. Пост остаётся на проверке.'
                    elif row['rubric'] == 'excuse' and storage.rubric_count_between('excuse', week_start, week_end):
                        response = 'Отмазка недели уже опубликована. Пост остаётся на проверке.'
                    elif datetime.fromisoformat(str(row["created_at"])) < datetime.now(timezone.utc) - timedelta(hours=48):
                        response = "Пост устарел: прошло больше 48 часов. Проверь новый материал."
                    elif not row['body_text'] or not (
                        valid_cost_body(item, row['source_text'], row['body_text'])
                        if row['rubric'] == 'cost' or (row['rubric'] == 'reader' and '\n\nРасчёт редакции: ' in row['body_text'])
                        else FactPreservationValidator().validate(row['source_text'], row['body_text']).valid
                    ):
                        response = "Проверка фактов не пройдена. Пост не опубликован."
                    else:
                        message_id = telegram.send(channel, str(row["post_html"]), preview_url=item.canonical_url or item.url)
                        storage.save(
                            item, "PUBLISHED", row["source_text"], row["mode"], str(row["topics"]).split(","),
                            telegram_message_id=message_id, post_html=str(row["post_html"]), rubric=row["rubric"],
                        )
                        storage.update_review(review_id, "PUBLISHED", message_id)
                        sent += 1
                        response = "Пост опубликован."
                telegram.send(editor_chat_id, response)
        storage.set_meta("telegram_update_offset", str(update_id + 1))
    return sent
