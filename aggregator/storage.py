from __future__ import annotations

import hashlib
import json
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
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS publications (
    event_key TEXT PRIMARY KEY, article_url TEXT NOT NULL,
    published_at TEXT NOT NULL, rubric TEXT, telegram_message_id INTEGER
);
CREATE TABLE IF NOT EXISTS review_queue (
    id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
    item_json TEXT NOT NULL, post_html TEXT NOT NULL, source_text TEXT NOT NULL,
    body_text TEXT NOT NULL,
    mode TEXT NOT NULL, topics TEXT NOT NULL, reasons TEXT NOT NULL,
    rubric TEXT, status TEXT NOT NULL DEFAULT 'PENDING',
    review_message_id INTEGER, public_message_id INTEGER,
    created_at TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        columns = {str(row["name"]) for row in self.connection.execute("PRAGMA table_info(articles)")}
        for name in ("content", "post_html", "rubric"):
            if name not in columns:
                self.connection.execute(f"ALTER TABLE articles ADD COLUMN {name} TEXT")
        if self.get_meta("publication_backfill") is None:
            self.connection.execute(
                """INSERT OR IGNORE INTO publications
                SELECT 'legacy:' || id, normalized_url, updated_at, rubric, telegram_message_id
                FROM articles WHERE status = 'PUBLISHED'"""
            )
            self.set_meta("publication_backfill", "done")
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def find_exact(self, normalized_url: str, normalized_title: str) -> str | None:
        row = self.connection.execute(
            """
            SELECT url FROM articles
            WHERE (normalized_url = ? OR normalized_title = ?)
              AND status IN ('PUBLISHED', 'SENT_TO_REVIEW', 'REVIEW_READY', 'DRY_RUN', 'REVIEW_PENDING', 'REJECTED')
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
                WHERE status IN ('PUBLISHED', 'SENT_TO_REVIEW', 'REVIEW_READY', 'DRY_RUN', 'REVIEW_PENDING', 'REJECTED')
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
            FROM publications
            WHERE published_at >= ? AND published_at < ?
            """,
            (start_utc.isoformat(), end_utc.isoformat()),
        ).fetchone()
        return int(row["total"])

    def recent_published(self, limit: int = 200) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                """
                SELECT title, normalized_title, source, url, canonical_url, content,
                       topics, telegram_message_id, updated_at, post_html, rubric
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
                       telegram_message_id, updated_at, post_html, rubric
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
        post_html: str = "",
        rubric: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        normalized = normalize_url(item.canonical_url or item.url)
        if status in {"FILTERED_OUT", "NOT_EMERGENCY"}:
            existing = self.find_by_normalized_url(normalized)
            if existing and existing["status"] in {
                "PUBLISHED", "SENT_TO_REVIEW", "REVIEW_READY", "DRY_RUN", "REVIEW_PENDING", "REJECTED"
            }:
                return
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
        if post_html or rubric:
            self.connection.execute(
                "UPDATE articles SET post_html = COALESCE(NULLIF(?, ''), post_html), rubric = COALESCE(?, rubric) WHERE normalized_url = ?",
                (post_html, rubric, normalized),
            )
        if status == "PUBLISHED":
            event_key = f"sent:{telegram_message_id}:{normalized}" if telegram_message_id else f"local:{now}:{normalized}"
            self.connection.execute(
                "INSERT OR IGNORE INTO publications VALUES (?, ?, ?, ?, ?)",
                (event_key, normalized, now, rubric, telegram_message_id),
            )
        self.connection.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO metadata VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value)
        )
        self.connection.commit()

    def rubric_count_between(self, rubric: str, start: datetime, end: datetime) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS total FROM publications WHERE rubric=? AND published_at>=? AND published_at<?",
            (rubric, start.isoformat(), end.isoformat()),
        ).fetchone()
        return int(row["total"])

    def enqueue_review(
        self, item: FeedItem, post: str, source_text: str, mode: str,
        topics: list[str], reasons: list[str], rubric: str | None = None, body_text: str = "",
    ) -> int:
        item_data = {"source": item.source, "title": item.title, "url": item.url,
                     "description": item.description, "canonical_url": item.canonical_url,
                     "published_at": item.published_at.isoformat() if item.published_at else None}
        fingerprint = hashlib.sha256(f"{normalize_url(item.url)}\n{post}".encode()).hexdigest()
        self.connection.execute(
            """INSERT OR IGNORE INTO review_queue
            (fingerprint, item_json, post_html, source_text, body_text, mode, topics, reasons, rubric, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fingerprint, json.dumps(item_data, ensure_ascii=False), post, source_text,
             body_text, mode, ",".join(topics), json.dumps(reasons, ensure_ascii=False), rubric,
             datetime.now(timezone.utc).isoformat()),
        )
        self.connection.commit()
        row = self.connection.execute("SELECT id FROM review_queue WHERE fingerprint=?", (fingerprint,)).fetchone()
        return int(row["id"])

    def pending_reviews(self, limit: int = 5) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            "SELECT * FROM review_queue WHERE status='PENDING' AND review_message_id IS NULL ORDER BY id LIMIT ?", (limit,)
        ))

    def get_review(self, review_id: int) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM review_queue WHERE id=?", (review_id,)).fetchone()

    def update_review(self, review_id: int, status: str, message_id: int | None = None) -> None:
        self.connection.execute(
            "UPDATE review_queue SET status=?, public_message_id=COALESCE(?, public_message_id) WHERE id=?", (status, message_id, review_id)
        )
        self.connection.commit()

    def mark_review_notified(self, review_id: int, message_id: int) -> None:
        self.connection.execute("UPDATE review_queue SET review_message_id=? WHERE id=?", (message_id, review_id))
        self.connection.commit()
