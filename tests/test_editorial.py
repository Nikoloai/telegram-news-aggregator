from datetime import datetime, timedelta, timezone

from aggregator.editorial import (
    ConfirmationLevel,
    confirmation_level,
    extract_title_quote,
    is_correction,
    is_emergency,
    promise_result_publication,
    related_publication,
    rotate_items,
    select_weekly_highlights,
)
from aggregator.models import FeedItem


def test_confirmation_levels_distinguish_sources_and_claims() -> None:
    assert confirmation_level("Событие подтверждено", ["Mediazona"]) == ConfirmationLevel.MULTIPLE_SOURCES
    assert confirmation_level("Ведомство заявило о событии", []) == ConfirmationLevel.PARTY_CLAIM
    assert confirmation_level("Произошло событие", []) == ConfirmationLevel.SINGLE_SOURCE


def test_emergency_requires_fresh_high_risk_story() -> None:
    now = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
    fresh = FeedItem(
        source="Test",
        title="Срочно: после взрыва началась эвакуация",
        url="https://example.org/fresh",
        published_at=now - timedelta(minutes=20),
    )
    stale = FeedItem(
        source="Test",
        title=fresh.title,
        url="https://example.org/stale",
        published_at=now - timedelta(hours=4),
    )

    assert is_emergency(fresh, ["war"], now)
    assert not is_emergency(fresh, ["sanctions_economy"], now)
    assert not is_emergency(stale, ["war"], now)


def test_correction_and_quote_detection() -> None:
    assert is_correction("Уточнение: данные в первой версии были ошибочными")
    assert extract_title_quote("Чиновник: «Система полностью готова к запуску сегодня»") == (
        "Система полностью готова к запуску сегодня"
    )


def test_related_and_promise_result_find_previous_story() -> None:
    rows = [
        {
            "title": "Власти пообещали запустить систему оплаты Мир до сентября",
            "content": "Проект должен был заработать в сентябре.",
            "topics": "state_policy",
            "updated_at": "2026-10-01T10:00:00+00:00",
        }
    ]
    current = "Власти не запустили систему оплаты Мир к сентябрю"

    assert related_publication(current, rows) is rows[0]
    assert promise_result_publication(current, "Запуск проекта сорван.", rows) is rows[0]


def test_rotation_promotes_the_slot_mode_without_dropping_items() -> None:
    items = [
        FeedItem("Test", "Госдума приняла закон", "https://example.org/analysis"),
        FeedItem("Test", "Власти показали импортозамещение", "https://example.org/satire"),
    ]

    rotated = rotate_items(items, slot=2)

    assert rotated[0].url.endswith("satire")
    assert {item.url for item in rotated} == {item.url for item in items}


def test_weekly_highlights_prioritize_consequential_and_distinct_stories() -> None:
    rows = [
        {
            "title": "Ведомство выпустило новый отчет",
            "topics": "state_policy",
            "updated_at": "2026-10-07T12:00:00+00:00",
        },
        {
            "title": "Суд арестовал журналиста в Москве",
            "topics": "repression",
            "updated_at": "2026-10-06T12:00:00+00:00",
        },
        {
            "title": "В Москве суд отправил журналиста под арест",
            "topics": "repression",
            "updated_at": "2026-10-05T12:00:00+00:00",
        },
    ]

    selected = select_weekly_highlights(rows, limit=2)

    assert selected[0]["topics"] == "repression"
    assert len(selected) == 2
    assert sum("журналиста" in str(row["title"]) for row in selected) == 1
