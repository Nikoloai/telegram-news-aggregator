from datetime import datetime, timedelta, timezone

from aggregator.models import FeedItem
from aggregator.storage import Storage


def test_storage_round_trip(tmp_path) -> None:
    storage = Storage(tmp_path / "state.db")
    item = FeedItem(source="Test", title="Суд арестовал Иванова", url="https://example.org/a?utm_source=x")
    storage.save(item, "REVIEW_READY", "text", "HARD_NEWS", ["repression"])
    assert storage.find_exact("https://example.org/a", "другой заголовок") == item.url
    assert storage.recent_titles()[0]["normalized_title"] == "суд арестовал иванова"
    storage.close()


def test_publication_events_count_same_url_corrections_separately(tmp_path) -> None:
    storage = Storage(tmp_path / "events.db")
    item = FeedItem("Test", "Первый выпуск", "https://example.org/same")
    storage.save(item, "PUBLISHED", telegram_message_id=1)
    item.title = "Уточнение: исправлены данные"
    storage.save(item, "PUBLISHED", telegram_message_id=2)
    storage.save(item, "PUBLISHED", telegram_message_id=2)  # Repeated persistence is idempotent.
    now = datetime.now(timezone.utc)
    assert storage.count_published_between(now-timedelta(minutes=1), now+timedelta(minutes=1)) == 2
    storage.close()


def test_old_database_migration_preserves_history_and_daily_counts(tmp_path) -> None:
    import sqlite3
    from aggregator.storage import SCHEMA
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA.replace("    content TEXT,\n", ""))
    now = datetime.now(timezone.utc)
    connection.execute(
        """INSERT INTO articles (url, normalized_url, title, normalized_title, source,
        discovered_at, status, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("https://example.org/legacy", "https://example.org/legacy", "Старый пост", "старый пост", "Test",
         now.isoformat(), "PUBLISHED", now.isoformat()),
    )
    connection.commit()
    connection.close()
    for _ in range(2):
        storage = Storage(path)
        assert storage.recent_published()[0]["title"] == "Старый пост"
        assert storage.recent_published()[0]["content"] is None
        assert storage.count_published_between(now-timedelta(minutes=1), now+timedelta(minutes=1)) == 1
        storage.close()


def test_count_published_between(tmp_path) -> None:
    storage = Storage(tmp_path / "state.db")
    published = FeedItem(source="Test", title="Опубликовано", url="https://example.org/published")
    review = FeedItem(source="Test", title="На проверке", url="https://example.org/review")
    storage.save(published, "PUBLISHED")
    storage.save(review, "REVIEW_READY")

    now = datetime.now(timezone.utc)
    assert storage.count_published_between(now - timedelta(minutes=1), now + timedelta(minutes=1)) == 1
    storage.close()


def test_recent_titles_ignores_filtered_items(tmp_path) -> None:
    storage = Storage(tmp_path / "state.db")
    storage.save(FeedItem(source="Test", title="Не публиковалось", url="https://example.org/no"), "FILTERED_OUT")
    storage.save(FeedItem(source="Test", title="Опубликовано", url="https://example.org/yes"), "PUBLISHED")

    assert [row["url"] for row in storage.recent_titles()] == ["https://example.org/yes"]
    storage.close()


def test_exact_lookup_ignores_emergency_rejection(tmp_path) -> None:
    storage = Storage(tmp_path / "state.db")
    item = FeedItem(source="Test", title="Обычная новость", url="https://example.org/ordinary")
    storage.save(item, "NOT_EMERGENCY")

    assert storage.find_exact("https://example.org/ordinary", "обычная новость") is None
    storage.close()


def test_emergency_rejection_does_not_overwrite_published_status(tmp_path) -> None:
    storage = Storage(tmp_path / "state.db")
    item = FeedItem(source="Test", title="Опубликовано", url="https://example.org/already-published")
    storage.save(item, "PUBLISHED", topics=["repression"])
    before = storage.find_by_normalized_url("https://example.org/already-published")
    rejected = FeedItem(source="Changed", title="Другой заголовок", url=item.url)
    storage.save(rejected, "NOT_EMERGENCY")

    row = storage.find_by_normalized_url("https://example.org/already-published")
    assert row is not None and before is not None
    assert row["status"] == "PUBLISHED"
    assert row["title"] == "Опубликовано"
    assert row["topics"] == "repression"
    assert row["updated_at"] == before["updated_at"]
    storage.close()


def test_same_url_can_store_a_changed_correction_title(tmp_path) -> None:
    storage = Storage(tmp_path / "state.db")
    original = FeedItem(source="Test", title="Первоначальные данные", url="https://example.org/correction")
    corrected = FeedItem(source="Test", title="Уточнение: исправлены данные", url=original.url)
    storage.save(original, "PUBLISHED")
    storage.save(corrected, "PUBLISHED")

    row = storage.find_by_normalized_url("https://example.org/correction")
    assert row is not None
    assert row["normalized_title"] == "уточнение исправлены данные"
    storage.close()


def test_recent_published_keeps_content_and_message_id(tmp_path) -> None:
    storage = Storage(tmp_path / "state.db")
    item = FeedItem(source="Test", title="Опубликовано", url="https://example.org/published")
    storage.save(
        item,
        "PUBLISHED",
        content="Полный текст",
        topics=["repression"],
        telegram_message_id=77,
    )

    row = storage.recent_published()[0]
    assert row["content"] == "Полный текст"
    assert row["telegram_message_id"] == 77
    assert storage.published_since(datetime.now(timezone.utc) - timedelta(minutes=1))[0]["title"] == "Опубликовано"
    storage.close()
