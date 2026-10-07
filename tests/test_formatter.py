from aggregator.formatter import EMOJI_PREFIXES, format_digest, format_post
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
