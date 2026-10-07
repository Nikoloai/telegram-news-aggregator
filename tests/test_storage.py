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
