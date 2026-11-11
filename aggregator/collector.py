from __future__ import annotations

import calendar
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import feedparser
import requests
from bs4 import BeautifulSoup

from .article import clean_html
from .filters import kaliningrad_context
from .models import FeedItem
from .readers import source_url

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


def parse_telegram_page(content: bytes | str, source: dict[str, Any]) -> list[FeedItem]:
    parts = urlsplit(str(source['feed_url']))
    channel_match = re.fullmatch(r'/s/([A-Za-z0-9_]{5,32})/?', parts.path)
    if parts.hostname != 't.me' or parts.scheme != 'https' or not channel_match:
        raise ValueError('Expected a public Telegram channel preview')
    channel = channel_match[1]
    soup = BeautifulSoup(content, 'html.parser')
    items = []
    for post in soup.select('.tgme_widget_message[data-post]'):
        identity = str(post.get('data-post', ''))
        if not re.fullmatch(re.escape(channel) + r'/\d+', identity, flags=re.I):
            continue
        # Replies include a separate, often truncated quote of an older post.
        # Never assign that quote the current post's date or identity.
        content_node = next((node for node in post.select('.tgme_widget_message_text')
                             if not node.find_parent(class_='tgme_widget_message_reply')
                             and not node.find_parent(class_='tgme_widget_message_link_preview')), None)
        time_node = post.select_one('.tgme_widget_message_date time[datetime]')
        if content_node is None or time_node is None:
            continue
        text = clean_html(str(content_node))
        if len(text) < 35:
            continue
        try:
            published = datetime.fromisoformat(str(time_node['datetime']).replace('Z', '+00:00'))
        except ValueError:
            continue
        if published.tzinfo is None:
            continue
        title = text if len(text) <= 180 else text[:177].rsplit(' ', 1)[0] + '…'
        url = 'https://t.me/' + identity
        # Newsrooms link their complete article. Fetch and deduplicate that primary URL.
        for link in [*content_node.select('a[href]'), *post.select('a.tgme_widget_message_link_preview[href]')]:
            allowed = source_url(str(link['href']), [source])
            if allowed and urlsplit(allowed[0]).hostname != 't.me':
                url = allowed[0]
                break
        forwarded = post.select_one('.tgme_widget_message_forwarded_from')
        if forwarded:
            text = f'Переслано из {forwarded.get_text(" ", strip=True)}.\n{text}'
        items.append(FeedItem(source['name'], title, url, text, published))
    return sorted(items, key=lambda item: item.published_at, reverse=True)


class FeedCollector:
    def __init__(self, timeout: int = 20, user_agent: str = "telegram-news-aggregator/0.1"):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    def collect(self, source: dict[str, Any]) -> list[FeedItem]:
        response = self.session.get(source["feed_url"], timeout=self.timeout)
        response.raise_for_status()
        if source.get('type') == 'telegram':
            items = parse_telegram_page(response.content, source)
        else:
            items = parse_feed(response.content, source["name"])
        for item in items:
            regional_text = f'{item.title} {clean_html(item.description)}'
            local_place = re.search(r'(?<![а-яё])(?:зеленоградск|гурьевск|светлогорск|пионерск|'
                                    r'светлый|светлого|светлом|советск|балтийск|черняховск|'
                                    r'гвардейск|мамонов|багратионовск|неман|славск|оз[её]рск|'
                                    r'краснознаменск|нестеров|правдинск|янтарн|заостров)',
                                    regional_text, flags=re.I)
            if source.get('region') == 'kaliningrad' and (kaliningrad_context(regional_text) or local_place):
                item.categories.append('Калининградская область')
            if source.get('requires_review') is True:
                item.categories.append('editorial_review')
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
