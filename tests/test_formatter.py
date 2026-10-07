from aggregator.formatter import EMOJI_PREFIXES, format_post


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
