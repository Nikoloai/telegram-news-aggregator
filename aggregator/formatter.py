from __future__ import annotations

import html
import re

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
) -> str:
    body = re.sub(r"[ \t]+", " ", body).strip()
    body = re.sub(r"^(?:⚡️?|🚨|❗️?|🔥|🔴|📌|💸|🧾|🤑|🤡|📺|🎪|⛓️|🪖|📉|🏛️|⚙️)\s*", "", body)
    prefix, rubric = _presentation(body, topics or [], mode, emoji_index)
    source_label = f"Источник: {source}"
    notes: list[str] = []
    if updated:
        notes.append("ОБНОВЛЕНО")
    if corroborated_by:
        notes.append("О том же сообщают: " + ", ".join(corroborated_by))
    header = f"<b>{html.escape(rubric)}</b>\n" if rubric else ""
    note_text = "\n".join(notes)
    overhead = len(prefix) + 1 + len(source_label) + len(note_text) + len(rubric or "") + 6
    available = max_chars - overhead
    if len(body) > available:
        clipped = body[: max(0, available - 1)].rsplit(" ", 1)[0].rstrip(".,;:") + "…"
    else:
        clipped = body
    escaped_url = html.escape(url, quote=True)
    footer = f'<a href="{escaped_url}">{html.escape(source_label)}</a>'
    note_block = f"\n\n{html.escape(note_text)}" if note_text else ""
    return f"{prefix} {header}{html.escape(clipped)}{note_block}\n\n{footer}"


def format_digest(
    posts: list[tuple[str, str, str]],
    title: str,
    max_chars: int = 3900,
) -> str:
    parts = [f"🗞 <b>{html.escape(title)}</b>"]
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
