from aggregator.filters import TopicFilter
from aggregator.models import FeedItem


def test_topic_filter_accepts_repression() -> None:
    result = TopicFilter().match(
        FeedItem(source="x", title="Российского журналиста арестовали по уголовному делу", url="https://example.org/1")
    )
    assert result.accepted is True
    assert "repression" in result.topics


def test_topic_filter_rejects_sport() -> None:
    result = TopicFilter().match(
        FeedItem(source="x", title="Футбольный клуб выиграл матч", url="https://example.org/2")
    )
    assert result.accepted is False


def test_topic_filter_rejects_unrelated_foreign_news() -> None:
    result = TopicFilter().match(
        FeedItem(
            source="x",
            title="В Индии изменили закон о выборах",
            url="https://example.org/3",
        )
    )
    assert result.accepted is False
    assert "not_russia_related" in result.excluded_by


def test_topic_filter_keeps_foreign_news_tied_to_russia() -> None:
    result = TopicFilter().match(
        FeedItem(
            source="x",
            title="Латвия ввела санкции против российского чиновника",
            url="https://example.org/4",
        )
    )
    assert result.accepted is True


def test_topic_filter_rejects_ukraine_only_news() -> None:
    result = TopicFilter().match(
        FeedItem(
            source="x",
            title="Украина сообщила об атаке дронов на порт",
            url="https://example.org/5",
        )
    )
    assert result.accepted is False
    assert "not_russia_related" in result.excluded_by


def test_topic_filter_accepts_russia_context_in_description() -> None:
    result = TopicFilter().match(
        FeedItem(
            source="x",
            title="Суд вынес приговор журналисту",
            description="Дело рассматривал российский суд в Москве.",
            url="https://example.org/6",
        )
    )
    assert result.accepted is True


def test_public_safety_in_novosibirsk_is_relevant_without_political_keywords():
    result = TopicFilter().match(FeedItem("Test", "В Новосибирске выявлена санитарная опасность",
                                         "https://example.org/safety"))
    assert result.accepted
    assert "public_safety" in result.topics
