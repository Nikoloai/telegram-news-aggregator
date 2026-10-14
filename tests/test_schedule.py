from datetime import date, datetime, timedelta, timezone

import main
import aggregator.editorial as editorial
import aggregator.storage as storage_module
from aggregator.schedule import PUBLICATION_SLOTS, publication_slots, due_posts, weekly_due, regular_release_due, spacing_ready
from aggregator.models import FeedItem
from aggregator.storage import Storage
from tests.test_pipeline import setup_pipeline


def test_ten_slots_and_daily_plan():
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
    now = now or datetime(2026, 10, 8, 16, 30, tzinfo=timezone.utc)

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


def test_delayed_release_publishes_one_message_and_repeat_publishes_none(monkeypatch, tmp_path):
    now = freeze_moscow_evening(monkeypatch)
    items = [FeedItem("Test", title, f"https://example.org/story-{index}", published_at=now)
             for index, title in enumerate(TITLES)]
    telegram = setup_pipeline(monkeypatch, items)
    monkeypatch.setenv("DAILY_POST_LIMIT", "10")
    args = main.parse_args(["--state", str(tmp_path / "state.db"), "--scheduled-release", "--max-items", "10"])
    first = main.run(args)
    second = main.run(args)
    assert first.sent == 1
    assert first.shortfall == 0
    assert second.sent == 0
    assert len([text for chat, text in telegram.messages if chat == "@channel"]) == 1


def test_digest_counts_as_one_real_message(tmp_path):
    storage = Storage(tmp_path / "state.db")
    for index in range(2):
        storage.save(FeedItem("Test", f"История {index}", f"https://example.org/{index}"),
                     "PUBLISHED", telegram_message_id=100)
    start, end = main.publication_day_window("Europe/Moscow")
    assert storage.count_published_between(start, end) == 1
    storage.close()


def test_one_story_fills_only_the_current_slot_without_a_false_shortfall(monkeypatch, tmp_path):
    now = freeze_moscow_evening(monkeypatch)
    telegram = setup_pipeline(monkeypatch, [FeedItem("Test", TITLES[0], "https://example.org/only", published_at=now)])
    monkeypatch.setenv("DAILY_POST_LIMIT", "10")
    args = main.parse_args(["--state", str(tmp_path / "state.db"), "--scheduled-release", "--max-items", "10"])
    result = main.run(args)
    assert result.sent == 1
    assert result.shortfall == 0
    assert len([text for chat, text in telegram.messages if chat == "@channel"]) == 1
    assert not any("Недобор плана" in text for chat, text in telegram.messages if chat == "123")


def test_existing_eight_posts_do_not_trigger_a_two_message_burst(monkeypatch, tmp_path):
    now = freeze_moscow_evening(monkeypatch)
    path = tmp_path / "state.db"
    storage = Storage(path)
    for index in range(8):
        storage.save(FeedItem("Old", f"Архивный выпуск {index}", f"https://example.org/old-{index}"),
                     "PUBLISHED", telegram_message_id=100 + index)
    storage.connection.execute('UPDATE publications SET published_at=?', ((now-timedelta(hours=2)).isoformat(),))
    storage.connection.commit()
    storage.close()
    items = [FeedItem("Test", title, f"https://example.org/new-{index}", published_at=now)
             for index, title in enumerate(TITLES)]
    telegram = setup_pipeline(monkeypatch, items)
    monkeypatch.setenv("DAILY_POST_LIMIT", "10")
    result = main.run(main.parse_args(["--state", str(path), "--scheduled-release"]))
    assert result.sent == 1
    assert result.shortfall == 0
    storage = Storage(path)
    assert storage.count_published_between(*main.publication_day_window("Europe/Moscow")) == 9
    storage.close()
    assert len([text for chat, text in telegram.messages if chat == "@channel"]) == 1


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


def test_normal_polling_can_fill_all_ten_slots_with_an_hour_between_posts():
    # Test the actual :17/:47 polling cadence across every daily jitter seed.
    zone = timezone(timedelta(hours=3))
    for offset in range(365):
        day = date(2026, 1, 1) + timedelta(days=offset)
        last = None
        sent = []
        for hour in range(8, 20):
            for minute in (17, 47):
                now = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute, tzinfo=zone)
                if regular_release_due(now, last):
                    sent.append(now)
                    last = now
        assert len(sent) == 10
        assert all(right-left >= timedelta(hours=1) for left, right in zip(sent, sent[1:]))


def test_spacing_requires_one_hour_even_across_midnight():
    now = datetime(2026, 10, 9, 0, 10, tzinfo=timezone.utc)
    assert spacing_ready(now, None)
    assert not spacing_ready(now, now-timedelta(minutes=59, seconds=59))
    assert spacing_ready(now, now-timedelta(hours=1))
    assert not spacing_ready(now, now+timedelta(minutes=1))


def test_night_and_previous_day_backlog_do_not_create_normal_posts():
    zone = timezone(timedelta(hours=3))
    last = datetime(2026, 10, 8, 10, 0, tzinfo=zone)
    for hour, minute in [(20, 0), (22, 30), (0, 1), (7, 59)]:
        now = datetime(2026, 10, 9, hour, minute, tzinfo=zone)
        assert not regular_release_due(now, last)
    first = publication_slots(date(2026, 10, 9))[0]
    assert regular_release_due(datetime(2026, 10, 9, *first, tzinfo=zone), last)


def test_filled_slot_does_not_publish_again_even_after_cooldown():
    day = date(2026, 10, 8)
    slots = publication_slots(day)
    hour, minute = next(left for left, right in zip(slots, slots[1:])
                        if right[0]*60+right[1] - left[0]*60-left[1] > 61)
    start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)
    assert (start+timedelta(minutes=61)).hour < 20
    assert not regular_release_due(start+timedelta(minutes=61), start)


def test_shortfall_counts_only_one_due_slot_not_all_missed_slots(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch)
    telegram = setup_pipeline(monkeypatch, [])
    result = main.run(main.parse_args(['--state', str(tmp_path/'state.db'), '--scheduled-release']))
    assert result.sent == 0 and result.shortfall == 1
    assert any('Недобор плана' in body for chat, body in telegram.messages if chat == '123')


def test_urgent_news_can_bypass_the_cooldown_but_remains_one_post(monkeypatch, tmp_path):
    now = freeze_moscow_evening(monkeypatch)
    path = tmp_path/'state.db'
    storage = Storage(path)
    storage.save(FeedItem('Old', TITLES[0], 'https://example.org/old'), 'PUBLISHED', telegram_message_id=50)
    storage.close()
    item = FeedItem('Test', 'В Москве после взрыва началась эвакуация', 'https://example.org/urgent', published_at=now)
    telegram = setup_pipeline(monkeypatch, [item])
    assert main.run(main.parse_args(['--state', str(path), '--scheduled-release'])).sent == 1
    assert any(chat == '@channel' and 'СРОЧНО' in body for chat, body in telegram.messages)


def test_quote_and_weekly_wait_instead_of_following_a_recent_post(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch)
    path = tmp_path/'state.db'
    storage = Storage(path)
    storage.save(FeedItem('Old', TITLES[0], 'https://example.org/old'), 'PUBLISHED', telegram_message_id=50)
    storage.close()
    telegram = setup_pipeline(monkeypatch, [])
    monkeypatch.setattr(main.FeedCollector, 'collect_all', lambda *args: (_ for _ in ()).throw(AssertionError('No collection while cooling down')))
    for option in ['--quote-of-day', '--weekly-digest']:
        assert main.run(main.parse_args(['--state', str(path), option])).sent == 0
    assert not telegram.messages


def test_failed_send_does_not_consume_the_slot_or_the_hour_pause(monkeypatch, tmp_path):
    now = freeze_moscow_evening(monkeypatch)
    item = FeedItem('Test', TITLES[0], 'https://example.org/retry', published_at=now)
    telegram = setup_pipeline(monkeypatch, [item])
    path = tmp_path/'state.db'
    send = telegram.send
    telegram.send = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('Test failure'))
    args = main.parse_args(['--state', str(path), '--scheduled-release'])
    assert main.run(args).sent == 0
    storage = Storage(path)
    assert storage.last_publication_at() is None
    storage.close()
    telegram.send = send
    assert main.run(args).sent == 1
    assert main.run(args).sent == 0


def test_late_night_run_does_not_dump_unfilled_daytime_posts(monkeypatch, tmp_path):
    now = freeze_moscow_evening(monkeypatch, datetime(2026, 10, 8, 19, 30, tzinfo=timezone.utc))
    items = [FeedItem('Test', title, f'https://example.org/{index}', published_at=now)
             for index, title in enumerate(TITLES)]
    telegram = setup_pipeline(monkeypatch, items)
    result = main.run(main.parse_args(['--state', str(tmp_path/'state.db'), '--scheduled-release', '--max-items', '10']))
    assert result.sent == 0 and result.shortfall == 0
    assert not telegram.messages
