from aggregator.formatter import EMOJI_PREFIXES, format_digest, format_post, format_weekly_digest
from aggregator.modes import RewriteMode


def test_format_post_rotates_emoji_and_hides_raw_url() -> None:
    post = format_post(
        "Жесткий & точный лид",
        "Настоящее Время",
        "https://example.org/news?id=1&source=rss",
        emoji_index=1,
    )

    assert post.startswith(f"{EMOJI_PREFIXES[1]} Жесткий &amp; точный лид")
    assert 'href="https://example.org/news?id=1&amp;source=rss"' in post
    assert ">Источник: Настоящее Время</a>" in post
    assert "\nhttps://example.org" not in post


def test_format_post_removes_model_emoji_before_adding_selected_one() -> None:
    post = format_post("⚡ Старый знак", "Источник", "https://example.org", emoji_index=2)
    assert post.startswith(f"{EMOJI_PREFIXES[2]} Старый знак")


def test_format_post_uses_satirical_rubric_and_topic_emoji() -> None:
    post = format_post(
        "Ведомство снова показало импортозамещение",
        "Источник",
        "https://example.org",
        emoji_index=0,
        topics=["propaganda"],
        mode=RewriteMode.SATIRICAL,
    )
    assert post.startswith("🤡 <b>Импортозамещение дня</b>")


def test_format_post_marks_update_and_corroboration() -> None:
    post = format_post(
        "Появились новые данные",
        "Источник",
        "https://example.org",
        corroborated_by=["Mediazona", "The Insider"],
        updated=True,
    )
    assert "ОБНОВЛЕНО" in post
    assert "О том же сообщают: Mediazona, The Insider" in post


def test_format_digest_hides_raw_urls() -> None:
    digest = format_digest(
        [
            ("Первая новость", "Mediazona", "https://example.org/one"),
            ("Вторая новость", "Meduza", "https://example.org/two"),
        ],
        "Утренний дайджест",
    )
    assert "<b>Утренний дайджест</b>" in digest
    assert '<a href="https://example.org/one">Mediazona</a>' in digest
    assert "\nhttps://example.org" not in digest


def test_format_post_adds_editorial_context_without_raw_link() -> None:
    post = format_post(
        "Система не заработала к обещанному сроку",
        "Источник",
        "https://example.org/current",
        confirmation="🟢 Подтверждено несколькими источниками",
        previous_title="Систему обещали запустить в сентябре",
        previous_url="https://t.me/example/42",
        promise_result=True,
    )

    assert post.startswith("📊 <b>Обещали / получилось</b>")
    assert "Подтверждено несколькими источниками" in post
    assert '<a href="https://t.me/example/42">Систему обещали запустить в сентябре</a>' in post
    assert "\nhttps://t.me/example/42" not in post


def test_format_post_correction_and_quote_rubrics() -> None:
    correction = format_post(
        "В первой версии было неверно указано имя",
        "Источник",
        "https://example.org",
        correction=True,
    )
    quote = format_post(
        "Чиновник объяснил решение",
        "Источник",
        "https://example.org",
        quote="Система полностью готова к запуску",
    )

    assert correction.startswith("🛠 <b>ИСПРАВЛЕНИЕ</b>")
    assert "<blockquote>Система полностью готова к запуску</blockquote>" in quote


def test_format_weekly_digest_links_channel_posts_and_summarizes_theme() -> None:
    digest = format_weekly_digest(
        [
            {
                "title": "Суд арестовал журналиста",
                "url": "https://example.org/story",
                "canonical_url": None,
                "topics": "repression",
                "telegram_message_id": 123,
            }
        ],
        "@example",
    )

    assert '<a href="https://t.me/example/123">Суд арестовал журналиста</a>' in digest
    assert "Сухой остаток" in digest
    assert "государственное давление" in digest


def test_digest_keeps_quote_context_and_confirmation() -> None:
    post = format_post(
        "Сервис не заработал вовремя", "Test", "https://example.org",
        quote="Мы запустим отечественный сервис вовремя",
        context="Обещали: «Сервис запустят до сентября».",
        confirmation="🟡 Пока один источник",
    )
    digest = format_digest([], "Вечерний дайджест", formatted_posts=[post])
    assert "Цитата дня" in digest
    assert "Обещали" in digest
    assert "Пока один источник" in digest
