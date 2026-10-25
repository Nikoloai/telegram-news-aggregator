from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path

from aggregator.longreads import eligible, load_queue, render_article


def test_mortgage_manuscript_stays_in_future_slot_without_immediate_override():
    root = Path('content/longreads')
    item = next(i for i in load_queue(root) if i['slug'] == 'family-mortgage-2026-10')
    assert item['publish_at'] == '2026-10-28T18:00:00+03:00'
    assert item['approved'] is True and item['publish_immediately'] is False
    assert not eligible(item, datetime(2026, 10, 26, 0, tzinfo=timezone.utc), True)
    assert not eligible(item, datetime(2026, 10, 28, 14, 59, tzinfo=timezone.utc), True)
    assert eligible(item, datetime(2026, 10, 28, 15, tzinfo=timezone.utc))
    assert not eligible(item, datetime(2026, 10, 28, 21, tzinfo=timezone.utc), True)
    text = (root / item['article']).read_text(encoding='utf-8')
    assert 1500 <= len(text.split()) <= 2500
    assert 600 <= len((root / item['announcement']).read_text(encoding='utf-8').strip()) <= 1000
    assert 'https://www.cbr.ru/Crosscut/LawActs/File/12256' in text
    render_article(text)  # Also enforces the Telegraph size limit.


def test_published_example_uses_remaining_balance_and_rate_ceiling():
    with localcontext() as context:
        context.prec = 40
        principal = Decimal(6000000)
        monthly = Decimal('0.06') / 12
        initial = principal * monthly / (1 - (1 + monthly) ** -360)
        balance = principal * (1 + monthly) ** 180 - initial * ((1 + monthly) ** 180 - 1) / monthly
        later_rate = Decimal('0.10') / 12
        later = balance * later_rate / (1 - (1 + later_rate) ** -180)
        assert round(initial) == 35973
        assert round(balance) == 4262931
        assert round(later) == 45810
        assert round(later - initial) == 9837
        assert round((later / initial - 1) * 100, 1) == Decimal('27.3')
    text = Path('content/longreads/03-family-mortgage.md').read_text(encoding='utf-8')
    for number in ('35 973', '4 262 931', '45 810', '9 837', '27,3%'):
        assert number in text
    assert 'не требует меньшего процента' in text
    assert 'не индивидуальная рекомендация' in text
