from __future__ import annotations

import html
import re


EMOJI_PREFIXES = ("⚡", "🚨", "❗️", "🔥", "🔴", "📌")


def format_post(body: str, source: str, url: str, emoji_index: int = 0, max_chars: int = 1500) -> str:
    body = re.sub(r"[ \t]+", " ", body).strip()
    body = re.sub(r"^(?:⚡️?|🚨|❗️?|🔥|🔴|📌)\s*", "", body)
    prefix = EMOJI_PREFIXES[emoji_index % len(EMOJI_PREFIXES)]
    source_label = f"Источник: {source}"
    available = max_chars - len(prefix) - 1 - len(source_label) - 2
    if len(body) > available:
        clipped = body[: max(0, available - 1)].rsplit(" ", 1)[0].rstrip(".,;:") + "…"
    else:
        clipped = body
    escaped_url = html.escape(url, quote=True)
    footer = f'<a href="{escaped_url}">{html.escape(source_label)}</a>'
    return f"{prefix} {html.escape(clipped)}\n\n{footer}"
