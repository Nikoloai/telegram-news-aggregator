from datetime import datetime, timezone

from main import digest_title, publication_day_window


def test_publication_day_window_uses_configured_timezone() -> None:
    start, end = publication_day_window(
        "Europe/Moscow",
        datetime(2026, 10, 6, 22, 30, tzinfo=timezone.utc),
    )

    assert start == datetime(2026, 10, 6, 21, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 10, 7, 21, 0, tzinfo=timezone.utc)


def test_digest_title_uses_moscow_time() -> None:
    assert digest_title("Europe/Moscow", datetime(2026, 10, 7, 5, 17, tzinfo=timezone.utc)) == "Утренний дайджест"
    assert digest_title("Europe/Moscow", datetime(2026, 10, 7, 17, 17, tzinfo=timezone.utc)) == "Вечерний дайджест"
