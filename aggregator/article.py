from __future__ import annotations

import logging
import re
from html import unescape

import requests
from bs4 import BeautifulSoup

from .models import Article, FeedItem

LOGGER = logging.getLogger(__name__)


def clean_html(value: str) -> str:
    soup = BeautifulSoup(value or "", "html.parser")
    text = re.sub(r"\s+", " ", unescape(soup.get_text(" ", strip=True))).strip()
    return re.sub(r"\s+([,.;:!?])", r"\1", text)


class ArticleFetcher:
    def __init__(self, timeout: int = 20, max_chars: int = 18_000):
        self.timeout = timeout
        self.max_chars = max_chars
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "telegram-news-aggregator/0.1"})

    def fetch(self, item: FeedItem) -> Article:
        fallback = clean_html(item.description)
        try:
            response = self.session.get(item.url, timeout=self.timeout)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            canonical = soup.select_one('link[rel="canonical"]')
            if canonical and canonical.get("href"):
                item.canonical_url = str(canonical["href"])
            root = soup.find("article") or soup.find("main") or soup.body
            paragraphs = root.find_all("p") if root else []
            text = "\n".join(
                part for part in (clean_html(str(p)) for p in paragraphs) if len(part) >= 35
            )
            if len(text) < 120:
                meta = soup.select_one('meta[property="og:description"]') or soup.select_one(
                    'meta[name="description"]'
                )
                meta_text = clean_html(str(meta.get("content", ""))) if meta else ""
                text = fallback or meta_text
        except requests.RequestException as exc:
            LOGGER.warning("Не удалось получить статью %s: %s", item.url, exc)
            text = fallback
        return Article(item=item, text=(text or fallback)[: self.max_chars])
