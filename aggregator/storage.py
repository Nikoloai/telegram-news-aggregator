from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
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
CREATE TABLE IF NOT EXISTS story_watches (
    id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
    source TEXT NOT NULL, telegram_message_id INTEGER, started_at TEXT NOT NULL,
    checkpoint INTEGER NOT NULL DEFAULT 7, next_check TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE'
);
CREATE TABLE IF NOT EXISTS reader_requests (
    id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING', created_at TEXT NOT NULL, public_url TEXT,
    angle TEXT NOT NULL DEFAULT 'facts'
);
CREATE TABLE IF NOT EXISTS reader_votes (
    sender_key TEXT NOT NULL, request_id INTEGER NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY(sender_key, request_id)
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
        reader_columns = {str(row['name']) for row in self.connection.execute('PRAGMA table_info(reader_requests)')}
        if 'public_url' not in reader_columns:
            self.connection.execute('ALTER TABLE reader_requests ADD COLUMN public_url TEXT')
        if 'angle' not in reader_columns:
            self.connection.execute("ALTER TABLE reader_requests ADD COLUMN angle TEXT NOT NULL DEFAULT 'facts'")
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
            SELECT COUNT(DISTINCT CASE WHEN telegram_message_id IS NOT NULL
                THEN 'telegram:' || telegram_message_id ELSE event_key END) AS total
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
                SELECT title, normalized_title, source, url, canonical_url, content, published_at,
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

    def feature_count_between(self, start: datetime, end: datetime) -> int:
        from .features import FEATURE_RUBRICS
        placeholders = ','.join('?' for _ in FEATURE_RUBRICS)
        row = self.connection.execute(
            f"SELECT COUNT(DISTINCT COALESCE(telegram_message_id, event_key)) AS n FROM publications "
            f"WHERE rubric IN ({placeholders}) AND published_at>=? AND published_at<?",
            (*FEATURE_RUBRICS, start.isoformat(), end.isoformat()),
        ).fetchone()
        return int(row['n'])

    def watch_story(self, row: object) -> None:
        from .features import worth_following
        if not worth_following(str(row['title']), str(row['topics'] or '').split(',')):
            return
        if self.connection.execute("SELECT COUNT(*) FROM story_watches WHERE status='ACTIVE'").fetchone()[0] >= 60:
            return
        started = datetime.fromisoformat(str(row['updated_at']))
        # Only recent stories are enrolled. Old historical rows must not flood the new desk.
        if started < datetime.now(timezone.utc) - timedelta(days=31):
            return
        self.connection.execute(
            "INSERT OR IGNORE INTO story_watches (url,title,source,telegram_message_id,started_at,next_check) "
            "VALUES (?,?,?,?,?,?)",
            (normalize_url(str(row['canonical_url'] or row['url'])), str(row['title']), str(row['source']),
             row['telegram_message_id'], started.isoformat(), (started + timedelta(days=7)).isoformat()),
        )
        self.connection.commit()

    def due_watches(self, now: datetime, limit: int = 10) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            "SELECT * FROM story_watches WHERE status='ACTIVE' AND next_check<=? ORDER BY next_check,id LIMIT ?",
            (now.isoformat(), limit),
        ))

    def finish_watch_checkpoint(self, watch_id: int) -> None:
        row = self.connection.execute("SELECT * FROM story_watches WHERE id=?", (watch_id,)).fetchone()
        if not row:
            return
        if row['checkpoint'] == 7:
            next_check = datetime.fromisoformat(row['started_at']) + timedelta(days=30)
            self.connection.execute("UPDATE story_watches SET checkpoint=30,next_check=? WHERE id=?",
                                    (next_check.isoformat(), watch_id))
        else:
            self.connection.execute("UPDATE story_watches SET status='DONE' WHERE id=?", (watch_id,))
        self.connection.commit()

    def add_reader_request(self, sender_key: str, url: str, source: str,
                           now: datetime, angle: str = 'facts') -> tuple[int | None, str]:
        """Persist ONLY a public source URL and keyed hashes, never the user's message/ID."""
        cutoff = (now - timedelta(days=30)).isoformat()
        self.connection.execute("DELETE FROM reader_votes WHERE created_at<?", (cutoff,))
        self.connection.execute("DELETE FROM reader_requests WHERE created_at<?", (cutoff,))
        self.connection.execute("DELETE FROM reader_votes WHERE request_id NOT IN (SELECT id FROM reader_requests)")
        count = self.connection.execute(
            "SELECT COUNT(*) FROM reader_votes WHERE sender_key=? AND created_at>=?",
            (sender_key, (now - timedelta(days=1)).isoformat()),
        ).fetchone()[0]
        if count >= 3:
            self.connection.commit()
            return None, 'limit'
        pending = self.connection.execute("SELECT COUNT(*) FROM reader_requests WHERE status='PENDING'").fetchone()[0]
        existing = self.connection.execute("SELECT * FROM reader_requests WHERE url=?", (url,)).fetchone()
        if pending >= 200 and not existing:
            self.connection.commit()
            return None, 'full'
        if angle not in {'facts', 'responsibility', 'contrast', 'cost'}:
            angle = 'facts'
        self.connection.execute("INSERT OR IGNORE INTO reader_requests (url,source,created_at,angle) VALUES (?,?,?,?)",
                                (url, source, now.isoformat(), angle))
        row = self.connection.execute("SELECT * FROM reader_requests WHERE url=?", (url,)).fetchone()
        if row['status'] != 'PENDING':
            self.connection.commit()
            return int(row['id']), 'covered'
        result = self.connection.execute(
            "INSERT OR IGNORE INTO reader_votes VALUES (?,?,?)", (sender_key, row['id'], now.isoformat()))
        self.connection.commit()
        return int(row['id']), 'accepted' if result.rowcount else 'duplicate'

    def prune_reader_requests(self, now: datetime) -> None:
        cutoff = (now - timedelta(days=30)).isoformat()
        self.connection.execute('DELETE FROM reader_requests WHERE created_at<?', (cutoff,))
        self.connection.execute('DELETE FROM reader_votes WHERE created_at<? OR request_id NOT IN (SELECT id FROM reader_requests)',
                                (cutoff,))
        self.connection.commit()

    def pending_reader_requests(self, limit: int = 30) -> list[sqlite3.Row]:
        return list(self.connection.execute(
            "SELECT r.*,COUNT(v.request_id) AS votes FROM reader_requests r "
            "LEFT JOIN reader_votes v ON v.request_id=r.id WHERE r.status='PENDING' "
            "GROUP BY r.id ORDER BY votes DESC,r.id LIMIT ?", (limit,),
        ))

    def index_reader_url(self, fingerprint: str, public_url: str) -> None:
        self.connection.execute("UPDATE reader_requests SET public_url=? WHERE url=?",
                                (normalize_url(public_url), fingerprint))
        self.connection.commit()

    def cover_reader_request(self, fingerprint: str) -> None:
        self.connection.execute("UPDATE reader_requests SET status='COVERED' WHERE url=?", (fingerprint,))
        self.connection.commit()

    def forget_reader(self, sender_key: str) -> None:
        self.connection.execute("DELETE FROM reader_votes WHERE sender_key=?", (sender_key,))
        self.connection.execute("DELETE FROM reader_requests WHERE status='PENDING' "
                                "AND id NOT IN (SELECT request_id FROM reader_votes)")
        self.connection.commit()
