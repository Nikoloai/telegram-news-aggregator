from aggregator.models import Article, FeedItem
from aggregator.modes import RewriteMode
from rewriter.fallback import FallbackRewriter


def test_fallback_does_not_repeat_title_at_start_of_summary() -> None:
    item = FeedItem(
        source="Test",
        title="Российские власти объявили новое правило",
        url="https://example.org",
        description=(
            "Российские власти объявили новое правило. "
            "Оно начинает действовать сегодня. Подробности опубликованы ведомством."
        ),
    )

    result = FallbackRewriter().rewrite(Article(item=item, text=""), RewriteMode.ANALYSIS)

    assert result.count("Российские власти объявили новое правило") == 1
    assert "Оно начинает действовать сегодня" in result
