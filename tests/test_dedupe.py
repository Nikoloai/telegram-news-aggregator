from aggregator.dedupe import Deduplicator, is_meaningful_update, normalize_title, normalize_url


class FakeStorage:
    def __init__(self) -> None:
        self.exact = None
        self.rows: list[dict[str, str]] = []

    def find_exact(self, normalized_url: str, normalized_title: str) -> str | None:
        return self.exact

    def recent_titles(self, limit: int = 500) -> list[dict[str, str]]:
        return self.rows[:limit]


def test_url_normalization_removes_tracking_and_fragment() -> None:
    assert normalize_url("HTTPS://WWW.Example.com/a/?utm_source=x&b=2&a=1#part") == (
        "https://example.com/a?a=1&b=2"
    )


def test_title_normalization() -> None:
    assert normalize_title("  Суд: «Иванова» арестовали! ") == "суд иванова арестовали"


def test_exact_dedupe() -> None:
    storage = FakeStorage()
    storage.exact = "https://example.org/old"
    duplicate, reason = Deduplicator(storage).find_duplicate("https://example.org/new", "Заголовок")
    assert duplicate is True
    assert reason and reason.startswith("exact:")


def test_fuzzy_dedupe() -> None:
    storage = FakeStorage()
    storage.rows = [
        {"url": "https://example.org/old", "normalized_title": "суд отправил иванова в сизо на два месяца"}
    ]
    duplicate, reason = Deduplicator(storage, fuzzy_threshold=75).find_duplicate(
        "https://other.example/new", "Иванова суд отправил в СИЗО на два месяца"
    )
    assert duplicate is True
    assert reason and reason.startswith("fuzzy:")


def test_same_named_event_dedupe() -> None:
    storage = FakeStorage()
    storage.rows = [{"url": "https://example.org/old", "normalized_title": "суд арестовал иванова"}]
    duplicate, reason = Deduplicator(storage).find_duplicate(
        "https://other.example/new", "Иванова отправили в СИЗО"
    )
    assert duplicate is True
    assert reason and reason.startswith("event:")


def test_unrelated_criminal_cases_are_not_duplicates() -> None:
    storage = FakeStorage()
    storage.rows = [
        {
            "url": "https://example.org/one",
            "normalized_title": "двоих подростков осудили за поджоги на железной дороге",
        }
    ]
    duplicate, _ = Deduplicator(storage).find_duplicate(
        "https://example.org/two", "на журналиста завели уголовное дело об оправдании терроризма"
    )
    assert duplicate is False


def test_meaningful_update_detection() -> None:
    assert is_meaningful_update("Стало известно о новых данных по делу") is True
    assert is_meaningful_update("Суд рассмотрел уголовное дело") is False
