from __future__ import annotations

import re


def format_post(body: str, source: str, url: str, max_chars: int = 1500) -> str:
    body = re.sub(r"[ \t]+", " ", body).strip()
    footer = f"\n\nИсточник: {source}\n{url}"
    available = max_chars - len(footer)
    if len(body) > available:
        clipped = body[: max(0, available - 1)].rsplit(" ", 1)[0].rstrip(".,;:") + "…"
    else:
        clipped = body
    return clipped + footer
