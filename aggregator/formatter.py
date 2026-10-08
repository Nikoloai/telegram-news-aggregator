from __future__ import annotations

import html
import re
from collections import Counter
from collections.abc import Mapping

from .article import clean_html
from .modes import RewriteMode


EMOJI_PREFIXES = ("⚡", "🚨", "❗️", "🔥", "🔴", "📌")
TOPIC_EMOJIS: dict[str, tuple[str, ...]] = {
    "war": ("⚡", "🚨", "🔴"),
    "military": ("⚡", "🪖", "🔴"),
    "repression": ("🚨", "⛓️", "❗️"),
    "corruption": ("💸", "🧾", "🤑"),
    "propaganda": ("🤡", "📺", "🎪"),
    "sanctions_economy": ("💸", "📉", "🧾"),
    "state_policy": ("🏛️", "📌", "⚙️"),
    "public_safety": ("⚠️", "🚨", "📌"),
}


def _presentation(body: str, topics: list[str], mode: RewriteMode, emoji_index: int) -> tuple[str, str | None]:
    emoji_options = next((TOPIC_EMOJIS[topic] for topic in topics if topic in TOPIC_EMOJIS), EMOJI_PREFIXES)
    emoji = emoji_options[emoji_index % len(emoji_options)]
    if mode == RewriteMode.SATIRICAL:
        lowered = body.lower()
        if "импортозамещ" in lowered:
            return emoji, "Импортозамещение дня"
        if "corruption" in topics:
            return emoji, "Страна возможностей"
        if "propaganda" in topics:
            return emoji, "Гойда дня"
        return emoji, "Стабильность"
    if mode == RewriteMode.IRONIC:
        return emoji, "Сухой остаток"
    return emoji, None


def format_post(
    body: str,
    source: str,
    url: str,
    emoji_index: int = 0,
    max_chars: int = 1000,
    topics: list[str] | None = None,
    mode: RewriteMode = RewriteMode.ANALYSIS,
    corroborated_by: list[str] | None = None,
    updated: bool = False,
    confirmation: str | None = None,
    previous_title: str | None = None,
    previous_url: str | None = None,
    correction: bool = False,
    promise_result: bool = False,
    quote: str | None = None,
    context: str | None = None,
    shared_origins: list[str] | None = None,
    emergency: bool = False,
) -> str:
    body = re.sub(r"[ \t]+", " ", body).strip()
    body = re.sub(r"^(?:⚡️?|🚨|❗️?|🔥|🔴|📌|💸|🧾|🤑|🤡|📺|🎪|⛓️|🪖|📉|🏛️|⚙️)\s*", "", body)
    prefix, rubric = _presentation(body, topics or [], mode, emoji_index)
    if correction:
        prefix, rubric = "🛠", "ИСПРАВЛЕНИЕ"
    elif promise_result:
        prefix, rubric = "📊", "Обещали / получилось"
    elif quote:
        prefix, rubric = "💬", "Цитата дня"
    elif emergency:
        prefix, rubric = "🚨", "СРОЧНО"
    source_label = f"Источник: {source}"
    notes: list[str] = []
    if updated:
        notes.append("<b>ОБНОВЛЕНО</b>")
    if confirmation:
        notes.append(f"<b>{html.escape(confirmation)}</b>")
    if corroborated_by:
        notes.append("О том же сообщают: " + html.escape(", ".join(corroborated_by)))
    if shared_origins:
        notes.append("Общий первоисточник: " + html.escape(", ".join(shared_origins)))
    if context:
        notes.append(html.escape(context))
    if previous_title and previous_url:
        notes.append(
            "Ранее: "
            f'<a href="{html.escape(previous_url, quote=True)}">{html.escape(previous_title)}</a>'
        )
    header = f"<b>{html.escape(rubric)}</b>\n" if rubric else ""
    quote_block = f"<blockquote>{html.escape(quote)}</blockquote>\n" if quote else ""
    note_text = "\n".join(notes)
    overhead = len(prefix) + len(source_label) + len(clean_html(note_text)) + len(rubric or "") + len(quote or "") + 12
    available = max_chars - overhead
    if len(body) > available:
        clipped = body[: max(0, available - 1)].rsplit(" ", 1)[0].rstrip(".,;:") + "…"
    else:
        clipped = body
    escaped_url = html.escape(url, quote=True)
    footer = f'<a href="{escaped_url}">{html.escape(source_label)}</a>'
    note_block = f"\n\n{note_text}" if note_text else ""
    return f"{prefix} {header}{quote_block}{html.escape(clipped)}{note_block}\n\n{footer}"


def format_digest(
    posts: list[tuple[str, str, str]],
    title: str,
    max_chars: int = 3900,
    formatted_posts: list[str] | None = None,
) -> str:
    parts = [f"🗞 <b>{html.escape(title)}</b>"]
    if formatted_posts is not None:
        for index, post in enumerate(formatted_posts, start=1):
            entry = f"<b>{index}.</b> {post}"
            if len(clean_html("\n\n".join(parts + [entry]))) > max_chars:
                raise ValueError("Подборка не помещается в Telegram; уменьшите число материалов")
            parts.append(entry)
        return "\n\n".join(parts)
    for index, (body, source, url) in enumerate(posts, start=1):
        paragraphs = [re.sub(r"\s+", " ", part).strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
        compact = paragraphs[0] if paragraphs else ""
        if len(compact) < 220 and len(paragraphs) > 1:
            next_sentence = re.split(r"(?<=[.!?])\s+", paragraphs[1], maxsplit=1)[0]
            compact = f"{compact}. {next_sentence}".replace("..", ".")
        if len(compact) > 360:
            compact = compact[:357].rsplit(" ", 1)[0].rstrip(".,;:") + "…"
        link = f'<a href="{html.escape(url, quote=True)}">{html.escape(source)}</a>'
        entry = f"<b>{index}.</b> {html.escape(compact)}\n{link}"
        if len("\n\n".join(parts + [entry])) > max_chars:
            break
        parts.append(entry)
    return "\n\n".join(parts)


def format_weekly_digest(
    rows: list[Mapping[str, object]],
    channel: str,
    max_items: int = 5,
) -> str:
    parts = ["🧾 <b>Итоги недели</b>"]
    topic_counts: Counter[str] = Counter()
    username = channel.lstrip("@")
    for index, row in enumerate(rows[:max_items], start=1):
        title = str(row["title"])[:500]
        message_id = row["telegram_message_id"]
        source_url = str(row["canonical_url"] or row["url"])
        url = f"https://t.me/{username}/{message_id}" if username and message_id else source_url
        parts.append(
            f'<b>{index}.</b> <a href="{html.escape(url, quote=True)}">{html.escape(title)}</a>'
        )
        topic_counts.update(filter(None, str(row["topics"] or "").split(",")))

    if topic_counts:
        dominant = topic_counts.most_common(1)[0][0]
        theme_names = {
            "war": "война и её последствия", "repression": "государственное давление и репрессии",
            "corruption": "коррупция", "propaganda": "пропаганда",
            "sanctions_economy": "экономика и санкции", "state_policy": "решения властей",
            "military": "военная политика", "media_pressure": "давление на СМИ",
            "public_safety": "безопасность людей и инфраструктура",
            "human_rights": "права человека", "politics": "политика",
        }
        conclusion = f"В этой подборке чаще всего встречается тема «{theme_names.get(dominant, dominant)}». Подробности и контекст — в постах выше."
        parts.append(f"<b>Сухой остаток:</b> {html.escape(conclusion)}")
    return "\n\n".join(parts)
