from aggregator.corroboration import corroborating_sources, stories_match
from aggregator.models import FeedItem


def test_stories_match_reworded_headlines() -> None:
    assert stories_match(
        "Российский суд отправил журналиста Иванова в СИЗО",
        "Журналиста Иванова арестовал российский суд",
    )


def test_corroborating_sources_are_unique_and_external() -> None:
    target = FeedItem("Mediazona", "Суд отправил Иванова в СИЗО", "https://a.example/1")
    items = [
        target,
        FeedItem("Meduza", "Иванова суд отправил в СИЗО", "https://b.example/1"),
        FeedItem("Meduza", "Суд арестовал Иванова", "https://b.example/2"),
        FeedItem("The Insider", "Другая новость", "https://c.example/1"),
    ]

    assert corroborating_sources(target, items) == ["Meduza"]
