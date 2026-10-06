from aggregator.models import FeedItem
from aggregator.storage import Storage


def test_storage_round_trip(tmp_path) -> None:
    storage = Storage(tmp_path / "state.db")
    item = FeedItem(source="Test", title="Суд арестовал Иванова", url="https://example.org/a?utm_source=x")
    storage.save(item, "REVIEW_READY", "text", "HARD_NEWS", ["repression"])
    assert storage.find_exact("https://example.org/a", "другой заголовок") == item.url
    assert storage.recent_titles()[0]["normalized_title"] == "суд арестовал иванова"
    storage.close()
