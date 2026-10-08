from datetime import date, datetime, timedelta, timezone

import main
import aggregator.editorial as editorial
import aggregator.storage as storage_module
from aggregator.schedule import PUBLICATION_SLOTS, publication_slots, due_posts, weekly_due
from aggregator.models import FeedItem
from aggregator.storage import Storage
from tests.test_pipeline import setup_pipeline


def test_ten_slots_and_cumulative_catchup():
    assert len(PUBLICATION_SLOTS) == 10
    day = date(2026, 10, 8)
    slots = publication_slots(day)
    first = datetime.combine(day, datetime.min.time()).replace(hour=slots[0][0], minute=slots[0][1])
    assert due_posts(first - timedelta(minutes=1)) == 0
    for index, (hour, minute) in enumerate(slots):
        assert due_posts(datetime(2026, 10, 8, hour, minute)) == index + 1
    assert due_posts(datetime(2026, 10, 8, 20, 0)) == 10
    assert due_posts(datetime(2026, 10, 9, 0, 17)) == 0
    assert weekly_due(datetime(2026, 10, 11, 21, 32))
    assert not weekly_due(datetime(2026, 10, 11, 21, 31))


def test_daily_times_are_stable_varied_and_bounded():
    first_day = date(2026, 1, 1)
    for index in range(365):
        day = first_day + timedelta(days=index)
        slots = publication_slots(day)
        minutes = [hour * 60 + minute for hour, minute in slots]
        gaps = [right - left for left, right in zip(minutes, minutes[1:])]
        assert slots == publication_slots(day)
        assert len(slots) == len(set(slots)) == 10
        assert minutes == sorted(minutes)
        assert all(8 * 60 <= minute < 20 * 60 for minute in minutes)
        assert all(46 <= gap <= 98 for gap in gaps)
        assert len(set(gaps)) > 1
    assert publication_slots(first_day) != publication_slots(first_day + timedelta(days=1))


def freeze_moscow_evening(monkeypatch, now=None):
    now = now or datetime(2026, 10, 8, 19, 30, tzinfo=timezone.utc)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(main, "datetime", FrozenDatetime)
    monkeypatch.setattr(editorial, "datetime", FrozenDatetime)
    monkeypatch.setattr(storage_module, "datetime", FrozenDatetime)
    return now


TITLES = [
    "Госдума России повысила налог на автомобили",
    "Российские чиновники представили отечественный реестр",
    "В Москве Роскомнадзор заблокировал интернет-платформу",
    "Коррупционное расследование выявило хищение бюджета в Дагестане",
    "Россия потеряла нефтяные доходы из-за санкций",
    "Мэр Калининграда подписал указ о строительстве поликлиники",
    "В Санкт-Петербурге журналист рассказал о давлении на СМИ",
    "В Чечне правозащитники заявили о нарушениях прав человека",
    "Кремль объявил изменения военной политики",
    "Правительство Татарстана сократило расходы на образование",
]


def test_delayed_release_publishes_ten_messages_and_repeat_publishes_none(monkeypatch, tmp_path):
    now = freeze_moscow_evening(monkeypatch)
    items = [FeedItem("Test", title, f"https://example.org/story-{index}", published_at=now)
             for index, title in enumerate(TITLES)]
    telegram = setup_pipeline(monkeypatch, items)
    monkeypatch.setenv("DAILY_POST_LIMIT", "10")
    args = main.parse_args(["--state", str(tmp_path / "state.db"), "--scheduled-release", "--max-items", "10"])
    first = main.run(args)
    second = main.run(args)
    assert first.sent == 10
    assert first.shortfall == 0
    assert second.sent == 0
    assert len([text for chat, text in telegram.messages if chat == "@channel"]) == 10


def test_digest_counts_as_one_real_message(tmp_path):
    storage = Storage(tmp_path / "state.db")
    for index in range(2):
        storage.save(FeedItem("Test", f"История {index}", f"https://example.org/{index}"),
                     "PUBLISHED", telegram_message_id=100)
    start, end = main.publication_day_window("Europe/Moscow")
    assert storage.count_published_between(start, end) == 1
    storage.close()


def test_news_shortage_is_reported_without_padding_or_duplicate_posts(monkeypatch, tmp_path):
    now = freeze_moscow_evening(monkeypatch)
    telegram = setup_pipeline(monkeypatch, [FeedItem("Test", TITLES[0], "https://example.org/only", published_at=now)])
    monkeypatch.setenv("DAILY_POST_LIMIT", "10")
    args = main.parse_args(["--state", str(tmp_path / "state.db"), "--scheduled-release", "--max-items", "10"])
    result = main.run(args)
    assert result.sent == 1
    assert result.shortfall == 9
    assert len([text for chat, text in telegram.messages if chat == "@channel"]) == 1
    assert any("Недобор плана" in text for chat, text in telegram.messages if chat == "123")


def test_existing_eight_posts_leave_room_for_exactly_two(monkeypatch, tmp_path):
    now = freeze_moscow_evening(monkeypatch)
    path = tmp_path / "state.db"
    storage = Storage(path)
    for index in range(8):
        storage.save(FeedItem("Old", f"Архивный выпуск {index}", f"https://example.org/old-{index}"),
                     "PUBLISHED", telegram_message_id=100 + index)
    storage.close()
    items = [FeedItem("Test", title, f"https://example.org/new-{index}", published_at=now)
             for index, title in enumerate(TITLES)]
    telegram = setup_pipeline(monkeypatch, items)
    monkeypatch.setenv("DAILY_POST_LIMIT", "10")
    result = main.run(main.parse_args(["--state", str(path), "--scheduled-release"]))
    assert result.sent == 2
    assert result.shortfall == 0
    storage = Storage(path)
    assert storage.count_published_between(*main.publication_day_window("Europe/Moscow")) == 10
    storage.close()
    assert len([text for chat, text in telegram.messages if chat == "@channel"]) == 2


def test_three_disputed_candidates_do_not_block_later_safe_news(monkeypatch, tmp_path):
    now = freeze_moscow_evening(monkeypatch)
    items = [FeedItem("Test", title, f"https://example.org/candidate-{index}", published_at=now)
             for index, title in enumerate(TITLES[:4])]
    telegram = setup_pipeline(monkeypatch, items)
    monkeypatch.setattr(main, "rank_news", lambda items, *args, **kwargs: items)
    disputed = set(TITLES[:3])
    monkeypatch.setattr(main, "review_reasons", lambda source, body, *args: ["Неподтверждённые сведения"] if body in disputed else [])
    result = main.run(main.parse_args(["--state", str(tmp_path / "state.db"), "--max-items", "1"]))
    public = [text for chat, text in telegram.messages if chat == "@channel"]
    assert result.sent == 1
    assert TITLES[3] in public[0]


def test_urgent_news_does_not_wait_for_first_daytime_slot(monkeypatch, tmp_path):
    now = freeze_moscow_evening(monkeypatch, datetime(2026, 10, 8, 5, 0, tzinfo=timezone.utc))
    item = FeedItem("Test", "В Москве после взрыва началась эвакуация", "https://example.org/urgent",
                    published_at=now)
    telegram = setup_pipeline(monkeypatch, [item])
    result = main.run(main.parse_args(["--state", str(tmp_path / "state.db"), "--scheduled-release"]))
    public = [text for chat, text in telegram.messages if chat == "@channel"]
    assert due_posts(now.astimezone(main.publication_zone("Europe/Moscow"))) == 0
    assert result.sent == 1
    assert "СРОЧНО" in public[0]


def test_urgent_story_in_an_ordinary_release_keeps_urgent_rubric(monkeypatch, tmp_path):
    now = freeze_moscow_evening(monkeypatch)
    item = FeedItem("Test", "В Москве после взрыва началась эвакуация", "https://example.org/urgent-day",
                    published_at=now)
    telegram = setup_pipeline(monkeypatch, [item])
    main.run(main.parse_args(["--state", str(tmp_path / "state.db"), "--scheduled-release"]))
    assert any("СРОЧНО" in text for chat, text in telegram.messages if chat == "@channel")
