from __future__ import annotations

import calendar
import logging
from datetime import datetime, timezone
from typing import Any

import feedparser
import requests

from .models import FeedItem

LOGGER = logging.getLogger(__name__)


def _published(entry: Any) -> datetime | None:
    value = entry.get("published_parsed") or entry.get("updated_parsed")
    if not value:
        return None
    return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)


def parse_feed(content: bytes | str, source: str) -> list[FeedItem]:
    parsed = feedparser.parse(content)
    items: list[FeedItem] = []
    for entry in parsed.entries:
        url = str(entry.get("link", "")).strip()
        title = str(entry.get("title", "")).strip()
        if not url or not title:
            continue
        tags = [str(tag.get("term", "")).strip() for tag in entry.get("tags", [])]
        content_blocks = entry.get("content", [])
        content = "\n".join(str(block.get("value", "")) for block in content_blocks)
        items.append(
            FeedItem(
                source=source,
                title=title,
                url=url,
                description=str(entry.get("summary") or entry.get("description") or content or ""),
                published_at=_published(entry),
                categories=[tag for tag in tags if tag],
            )
        )
    return items


class FeedCollector:
    def __init__(self, timeout: int = 20, user_agent: str = "telegram-news-aggregator/0.1"):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    def collect(self, source: dict[str, Any]) -> list[FeedItem]:
        response = self.session.get(source["feed_url"], timeout=self.timeout)
        response.raise_for_status()
        items = parse_feed(response.content, source["name"])
        limit = int(source.get("max_items", 30))
        LOGGER.info("Источник %s: получено %d материалов", source["name"], len(items))
        return items[:limit]

    def collect_all(self, sources: list[dict[str, Any]]) -> list[FeedItem]:
        result: list[FeedItem] = []
        for source in sources:
            if not source.get("enabled", True):
                continue
            try:
                result.extend(self.collect(source))
            except (requests.RequestException, ValueError) as exc:
                LOGGER.error("Источник %s недоступен: %s", source.get("name", "?"), exc)
        return result
