from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .dedupe import normalize_title, normalize_url
from .models import FeedItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL,
    canonical_url TEXT,
    normalized_url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    source TEXT NOT NULL,
    published_at TEXT,
    discovered_at TEXT NOT NULL,
    status TEXT NOT NULL,
    telegram_message_id INTEGER,
    content_hash TEXT,
    rewrite_mode TEXT,
    topics TEXT,
    error TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_title ON articles(normalized_title);
CREATE INDEX IF NOT EXISTS idx_articles_discovered ON articles(discovered_at DESC);
"""


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def find_exact(self, normalized_url: str, normalized_title: str) -> str | None:
        row = self.connection.execute(
            "SELECT url FROM articles WHERE normalized_url = ? OR normalized_title = ? LIMIT 1",
            (normalized_url, normalized_title),
        ).fetchone()
        return str(row["url"]) if row else None

    def recent_titles(self, limit: int = 500) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                "SELECT url, normalized_title FROM articles ORDER BY discovered_at DESC LIMIT ?", (limit,)
            )
        )

    def save(
        self,
        item: FeedItem,
        status: str,
        content: str = "",
        rewrite_mode: str | None = None,
        topics: list[str] | None = None,
        error: str | None = None,
        telegram_message_id: int | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        normalized = normalize_url(item.canonical_url or item.url)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest() if content else None
        self.connection.execute(
            """
            INSERT INTO articles (
                url, canonical_url, normalized_url, title, normalized_title, source,
                published_at, discovered_at, status, telegram_message_id, content_hash,
                rewrite_mode, topics, error, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(normalized_url) DO UPDATE SET
                status=excluded.status,
                telegram_message_id=COALESCE(excluded.telegram_message_id, articles.telegram_message_id),
                content_hash=COALESCE(excluded.content_hash, articles.content_hash),
                rewrite_mode=COALESCE(excluded.rewrite_mode, articles.rewrite_mode),
                topics=COALESCE(excluded.topics, articles.topics),
                error=excluded.error,
                updated_at=excluded.updated_at
            """,
            (
                item.url,
                item.canonical_url,
                normalized,
                item.title,
                normalize_title(item.title),
                item.source,
                item.published_at.isoformat() if item.published_at else None,
                now,
                status,
                telegram_message_id,
                content_hash,
                rewrite_mode,
                ",".join(topics or []),
                error,
                now,
            ),
        )
        self.connection.commit()
