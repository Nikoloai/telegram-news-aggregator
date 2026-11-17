"""End-to-end safety gates for authored satire, with no live LLM or Telegram calls."""
from datetime import datetime, timezone

import pytest

import main
from aggregator.corroboration import source_evidence
from aggregator.editorial import related_publication
from aggregator.models import Article, FeedItem
from aggregator.voice_quality import SatireReview, parse_satire_review, satirical_voice_issues
from rewriter.llm import LLMRewriter, EDITOR_REVIEW_PROMPT
from tests.test_pipeline import setup_pipeline


def test_review_must_be_explicit_structured_editorial_decision():
    assert parse_satire_review('{"decision":"approve","reason":"Конкретное противоречие","revised_text":""}').decision == "approve"
    replacement = parse_satire_review('{"decision":"revise","reason":"Нейтральное изложение","revised_text":"Полный исправленный текст."}')
    assert replacement.revised_text == "Полный исправленный текст."
    for value in (
        "ok",
        '{"decision":"maybe","reason":"интересно"}',
        '{"decision":"revise","reason":"пересказ","revised_text":""}',
        '{"decision":"approve","reason":"","revised_text":""}',
        '{"decision":"approve","reason":"да","revised_text":"подмена"}',
    ):
        with pytest.raises(ValueError):
            parse_satire_review(value)


def test_copy_guard_blocks_news_copy_and_template_moral():
    title = "В России чиновники подняли тариф"
    source = "В России чиновники подняли тариф. Подорожание вступило в силу."
    assert satirical_voice_issues(title, source, title)
    text = title + "\n\n" + "Нам остаётся только надеяться, что всё наладится. " * 6
    assert any("Шаблонная" in x for x in satirical_voice_issues(title, source, text))


def test_editor_reviews_original_draft_with_source_not_only_the_last_paragraph(monkeypatch):
    requests = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content":
                '{"decision":"reject","reason":"Пресс-релиз с одной шуткой","revised_text":""}'}}]}

    monkeypatch.setattr("rewriter.llm.requests.post", lambda *args, **kwargs: requests.append(kwargs) or Response())
    writer = LLMRewriter("openai", "test-key", "test-model")
    item = FeedItem("RUGRAD", "В Калининграде изменили бюджет", "https://example.org/news")
    result = writer.review_satire(Article(item, "Бюджет изменили по решению правительства."), "Власти сообщили о бюджете.")
    assert result.decision == "reject"
    assert requests[0]["json"]["temperature"] == 0.15
    assert "пресс" in EDITOR_REVIEW_PROMPT.lower() or "прес" in EDITOR_REVIEW_PROMPT.lower()
    assert "Фактический материал" in requests[0]["json"]["messages"][1]["content"]
    assert "Черновик «Гвоздики»" in requests[0]["json"]["messages"][1]["content"]


SOURCE = "В России тариф вырос со 100 до 120 рублей в месяц."
ORIGINAL = (
    "В России тариф вырос со 100 до 120 рублей в месяц. Цифры поменялись в одну сторону.\n\n"
    "В решении всё выглядит аккуратно: прежняя сумма была меньше, новая больше. "
    "В документе достаточно одной строчки, а в семейном бюджете эта строчка теперь надолго."
)
REVISED = (
    "В России тариф вырос со 100 до 120 рублей в месяц. Один росчерк — и платёж уже выше. "
    "Для бумаги это всего лишь смена строки, никаких лишних движений.\n\n"
    "У чиновничьего решения очень удобная физика: оно мгновенно проходит через кабинет "
    "и материализуется на чужой квитанции. У гражданина, правда, кнопки «отменить» не предусмотрено."
)


def _pipeline(monkeypatch, tmp_path, decision: SatireReview, initial: str = ORIGINAL):
    monkeypatch.setenv("EDITORIAL_STYLE", "satirical")
    item = FeedItem("Test", "В России увеличили тариф", "https://example.org/tariff",
                    SOURCE, datetime.now(timezone.utc))
    telegram = setup_pipeline(monkeypatch, [item])
    writer = LLMRewriter("openai", "test-only", "test-model")
    called = []
    monkeypatch.setattr(writer, "rewrite", lambda article, mode: initial)
    monkeypatch.setattr(writer, "review_satire", lambda article, body: called.append(body) or decision)
    monkeypatch.setattr(main, "choose_rewriter", lambda: writer)
    result = main.run(main.parse_args(["--state", str(tmp_path / "state.db"), "--max-items", "1"]))
    return telegram, result, called


def test_weak_satire_is_rejected_before_telegram(monkeypatch, tmp_path):
    telegram, result, called = _pipeline(
        monkeypatch, tmp_path, SatireReview("reject", "Пересказ вместо авторского голоса"))
    assert called
    assert result.sent == 0
    assert not any(chat == "@channel" for chat, _ in telegram.messages)


def test_editor_can_improve_authored_news_and_revalidation_protects_facts(monkeypatch, tmp_path):
    telegram, result, called = _pipeline(
        monkeypatch, tmp_path, SatireReview("revise", "Нужен авторский угол", REVISED))
    assert called
    assert result.sent == 1
    posts = [text for chat, text in telegram.messages if chat == "@channel"]
    assert len(posts) == 1 and "чужой квитанции" in posts[0]
    assert "Источник: Test" in posts[0]


def test_editor_cannot_introduce_new_numbers(monkeypatch, tmp_path):
    altered = REVISED.replace("квитанции", "квитанции с налогом в 9999 рублей")
    telegram, result, called = _pipeline(
        monkeypatch, tmp_path, SatireReview("revise", "Поменяли числа", altered))
    assert called
    assert result.sent == 0
    assert result.validation_failed == 1
    assert not telegram.messages


def test_fact_changing_original_is_not_replaced_with_unattributed_fallback(monkeypatch, tmp_path):
    telegram, result, called = _pipeline(
        monkeypatch, tmp_path, SatireReview("approve", "Принять"),
        initial=ORIGINAL.replace("120 рублей", "999 рублей"))
    assert result.sent == 0 and result.validation_failed == 1
    assert not called
    assert not telegram.messages


def test_different_ukrainian_cities_not_same_story():
    a = FeedItem("Первое издание", "Россия ударила по складу в Одессе. Есть погибшие", "https://example.org/a")
    b = FeedItem("Второе издание", "Россия ударила по маршрутке в Краматорске. Есть погибшие", "https://example.org/b")
    assert source_evidence(a, [a, b]).sources == []
    assert related_publication(a.title, [{"title": b.title}]) is None


def test_previous_strike_is_not_linked_as_an_update_of_a_new_one_even_same_city():
    title = "Россия ударила по школе в Одессе. Есть погибшие"
    old = "Россия ударила по складу в Одессе. Есть погибшие"
    assert related_publication(title, [{"title": old}]) is None
