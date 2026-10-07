from datetime import datetime, timezone

import main
from aggregator.models import Article, FeedItem
from aggregator.storage import Storage
from tests.test_review import FakeTelegram


def setup_pipeline(monkeypatch, items):
    telegram = FakeTelegram()
    monkeypatch.setenv("REVIEW_MODE", "false")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-only")
    monkeypatch.setenv("TELEGRAM_CHANNEL", "@channel")
    monkeypatch.setenv("EDITOR_CHAT_ID", "123")
    monkeypatch.setattr(main, "TelegramClient", lambda token: telegram)
    monkeypatch.setattr(main, "choose_rewriter", main.FallbackRewriter)
    monkeypatch.setattr(main.FeedCollector, "collect_all", lambda self, sources: items)
    monkeypatch.setattr(main.ArticleFetcher, "fetch", lambda self, item: Article(item, item.description or item.title))
    return telegram


def test_conflicting_story_is_held_and_not_leaked_into_digest(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    items = [
        FeedItem("First", "В Москве после взрыва погибли 3 человека", "https://example.org/a", published_at=now),
        FeedItem("Second", "В Москве после взрыва погибли 7 человек", "https://example.org/b", published_at=now),
        FeedItem("Third", "Госдума России приняла закон о налогах", "https://example.org/c", published_at=now),
    ]
    telegram = setup_pipeline(monkeypatch, items)
    main.run(main.parse_args(["--state", str(tmp_path / "state.db"), "--digest", "--max-items", "2"]))
    public = [text for chat, text in telegram.messages if chat == "@channel"]
    assert len(public) == 1
    assert "Госдума" in public[0]
    assert "погибли" not in public[0]
    assert "Пока один источник" in public[0]
    assert any(chat == "123" and "/publish" in text for chat, text in telegram.messages)


def test_quote_is_sent_only_once_per_day(monkeypatch, tmp_path):
    item = FeedItem("Test", "Чиновник в России заявил: «Отечественная система полностью готова к запуску»",
                    "https://example.org/quote", description="Чиновник представил импортозамещение.",
                    published_at=datetime.now(timezone.utc))
    telegram = setup_pipeline(monkeypatch, [item])
    args = main.parse_args(["--state", str(tmp_path / "state.db"), "--quote-of-day", "--max-items", "1"])
    main.run(args)
    item.url = "https://example.org/another-quote"
    main.run(args)
    public = [text for chat, text in telegram.messages if chat == "@channel"]
    assert len(public) == 1
    assert "Цитата дня" in public[0]


def test_weekly_digest_is_idempotent_and_does_not_collect_news(monkeypatch, tmp_path):
    path = tmp_path / "state.db"
    storage = Storage(path)
    storage.save(FeedItem("Test", "Госдума России приняла закон", "https://example.org/old"),
                 "PUBLISHED", topics=["state_policy"], telegram_message_id=90)
    storage.close()
    telegram = setup_pipeline(monkeypatch, [])
    args = main.parse_args(["--state", str(path), "--weekly-digest"])
    main.run(args)
    main.run(args)
    public = [text for chat, text in telegram.messages if chat == "@channel"]
    assert len(public) == 1
    assert "Итоги недели" in public[0]
    assert 'href="https://t.me/channel/90"' in public[0]


def test_same_url_correction_is_sent_once_for_each_changed_version(monkeypatch, tmp_path):
    item = FeedItem("Test", "Уточнение: в России принят закон о налогах", "https://example.org/law",
                    description="Госдума приняла закон о налогах.", published_at=datetime.now(timezone.utc))
    telegram = setup_pipeline(monkeypatch, [item])
    args = main.parse_args(["--state", str(tmp_path / "state.db"), "--max-items", "1"])
    main.run(args)
    main.run(args)
    item.description = "Госдума уточнила срок вступления закона о налогах."
    main.run(args)
    main.run(args)
    public = [text for chat, text in telegram.messages if chat == "@channel"]
    assert len(public) == 2
    assert all("ИСПРАВЛЕНИЕ" in text for text in public)
    assert "срок вступления" in public[1]


def test_promise_result_survives_dedupe_but_repeated_result_does_not(monkeypatch, tmp_path):
    path = tmp_path / "state.db"
    storage = Storage(path)
    storage.save(FeedItem("Test", "Минцифры России обещало запустить отечественный сервис ГосКлюч",
                          "https://example.org/promise"), "PUBLISHED",
                 "Минцифры России обещало запустить отечественный сервис ГосКлюч.",
                 topics=["state_policy"], telegram_message_id=90)
    storage.close()
    item = FeedItem("Test", "Минцифры России запустило отечественный сервис ГосКлюч",
                    "https://example.org/result", description="Сервис ГосКлюч заработал.",
                    published_at=datetime.now(timezone.utc))
    telegram = setup_pipeline(monkeypatch, [item])
    args = main.parse_args(["--state", str(path), "--max-items", "1"])
    main.run(args)
    item.url = "https://example.org/result-another-outlet"
    main.run(args)
    public = [text for chat, text in telegram.messages if chat == "@channel"]
    assert len(public) == 1
    assert "Обещали / получилось" in public[0]
    assert "обещало запустить" in public[0]
