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
    content TEXT,
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
        columns = {str(row["name"]) for row in self.connection.execute("PRAGMA table_info(articles)")}
        if "content" not in columns:
            self.connection.execute("ALTER TABLE articles ADD COLUMN content TEXT")
            self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def find_exact(self, normalized_url: str, normalized_title: str) -> str | None:
        row = self.connection.execute(
            """
            SELECT url FROM articles
            WHERE (normalized_url = ? OR normalized_title = ?)
              AND status IN ('PUBLISHED', 'SENT_TO_REVIEW', 'REVIEW_READY', 'DRY_RUN')
            LIMIT 1
            """,
            (normalized_url, normalized_title),
        ).fetchone()
        return str(row["url"]) if row else None

    def find_by_normalized_url(self, normalized_url: str) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT title, normalized_title, content, status, topics, updated_at
            FROM articles
            WHERE normalized_url = ?
            LIMIT 1
            """,
            (normalized_url,),
        ).fetchone()

    def recent_titles(self, limit: int = 500) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT url, normalized_title
                FROM articles
                WHERE status IN ('PUBLISHED', 'SENT_TO_REVIEW', 'REVIEW_READY', 'DRY_RUN')
                ORDER BY discovered_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        )

    def count_published_between(self, start_utc: datetime, end_utc: datetime) -> int:
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM articles
            WHERE status = 'PUBLISHED' AND updated_at >= ? AND updated_at < ?
            """,
            (start_utc.isoformat(), end_utc.isoformat()),
        ).fetchone()
        return int(row["total"])

    def recent_published(self, limit: int = 200) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT title, normalized_title, source, url, canonical_url, content,
                       topics, telegram_message_id, updated_at
                FROM articles
                WHERE status = 'PUBLISHED'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        )

    def published_since(self, start_utc: datetime, limit: int = 100) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT title, source, url, canonical_url, content, topics,
                       telegram_message_id, updated_at
                FROM articles
                WHERE status = 'PUBLISHED' AND updated_at >= ?
                  AND COALESCE(topics, '') NOT LIKE '%weekly_digest%'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (start_utc.isoformat(), limit),
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
                published_at, discovered_at, status, telegram_message_id, content, content_hash,
                rewrite_mode, topics, error, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(normalized_url) DO UPDATE SET
                title=CASE
                    WHEN articles.status IN ('PUBLISHED', 'SENT_TO_REVIEW', 'REVIEW_READY', 'DRY_RUN')
                         AND excluded.status IN ('FILTERED_OUT', 'NOT_EMERGENCY')
                    THEN articles.title ELSE excluded.title END,
                normalized_title=CASE
                    WHEN articles.status IN ('PUBLISHED', 'SENT_TO_REVIEW', 'REVIEW_READY', 'DRY_RUN')
                         AND excluded.status IN ('FILTERED_OUT', 'NOT_EMERGENCY')
                    THEN articles.normalized_title ELSE excluded.normalized_title END,
                source=CASE
                    WHEN articles.status IN ('PUBLISHED', 'SENT_TO_REVIEW', 'REVIEW_READY', 'DRY_RUN')
                         AND excluded.status IN ('FILTERED_OUT', 'NOT_EMERGENCY')
                    THEN articles.source ELSE excluded.source END,
                published_at=CASE
                    WHEN articles.status IN ('PUBLISHED', 'SENT_TO_REVIEW', 'REVIEW_READY', 'DRY_RUN')
                         AND excluded.status IN ('FILTERED_OUT', 'NOT_EMERGENCY')
                    THEN articles.published_at
                    ELSE COALESCE(excluded.published_at, articles.published_at) END,
                status=CASE
                    WHEN articles.status IN ('PUBLISHED', 'SENT_TO_REVIEW', 'REVIEW_READY', 'DRY_RUN')
                         AND excluded.status IN ('FILTERED_OUT', 'NOT_EMERGENCY')
                    THEN articles.status
                    ELSE excluded.status
                END,
                telegram_message_id=COALESCE(excluded.telegram_message_id, articles.telegram_message_id),
                content=COALESCE(excluded.content, articles.content),
                content_hash=COALESCE(excluded.content_hash, articles.content_hash),
                rewrite_mode=COALESCE(excluded.rewrite_mode, articles.rewrite_mode),
                topics=CASE
                    WHEN articles.status IN ('PUBLISHED', 'SENT_TO_REVIEW', 'REVIEW_READY', 'DRY_RUN')
                         AND excluded.status IN ('FILTERED_OUT', 'NOT_EMERGENCY')
                    THEN articles.topics ELSE COALESCE(excluded.topics, articles.topics) END,
                error=excluded.error,
                updated_at=CASE
                    WHEN articles.status IN ('PUBLISHED', 'SENT_TO_REVIEW', 'REVIEW_READY', 'DRY_RUN')
                         AND excluded.status IN ('FILTERED_OUT', 'NOT_EMERGENCY')
                    THEN articles.updated_at ELSE excluded.updated_at END
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
                content or None,
                content_hash,
                rewrite_mode,
                ",".join(topics or []),
                error,
                now,
            ),
        )
        self.connection.commit()
