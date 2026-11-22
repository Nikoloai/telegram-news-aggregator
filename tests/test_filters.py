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


def test_baltiysk_city_is_regional_but_baltic_sea_is_not_automatically_russia():
    for title in ("В Балтийске объявлена воздушная тревога", "Беспилотники атаковали Балтийск"):
        item = FeedItem("Test", title, "https://example.org/regional")
        result = TopicFilter().match(item)
        assert result.accepted and "kaliningrad" in result.topics
    assert not TopicFilter().match(FeedItem("Test", "В Балтийском море объявили тревогу",
                                           "https://example.org/sea")).accepted


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


def test_routine_local_accidents_do_not_enter_political_feed():
    for title in (
        "В Калининграде при ДТП пострадал один водитель",
        "На улице в Калининграде сгорел припаркованный автомобиль",
        "В Новосибирске отключили воду в одном доме из-за аварии",
    ):
        match = TopicFilter().match(FeedItem("Test", title, "https://example.org/incident"))
        assert not match.accepted, title
        assert "routine_minor_incident" in match.excluded_by


def test_major_outages_and_public_accountability_are_still_relevant():
    cases = (
        ("Крупная авария в Калининграде: тысячи людей остались без отопления", ""),
        ("В Калининграде авария на теплотрассе", "Несколько районов остались без отопления"),
        ("В Калининграде пожар в больнице, началась эвакуация", ""),
        ("В Калининграде авария на дороге", "После проверки выявлена халатность чиновников"),
    )
    for title, description in cases:
        assert TopicFilter().match(FeedItem("Test", title, "https://example.org/major",
                                            description=description)).accepted


def test_political_and_repression_news_not_blocked_by_incident_gate():
    item = FeedItem("Test", "В Калининграде арестовали журналиста после расследования аварии",
                    "https://example.org/repression")
    result = TopicFilter().match(item)
    assert result.accepted
    assert "repression" in result.topics
