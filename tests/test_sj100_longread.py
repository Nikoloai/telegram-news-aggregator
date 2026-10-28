import re
from datetime import datetime, timezone
from pathlib import Path

from aggregator.longreads import eligible, load_queue, render_article


def test_sj100_longread_is_valid_and_waits_for_sunday_moscow_slot():
    root = Path('content/longreads')
    item = next(i for i in load_queue(root) if i['slug'] == 'sj100-deadlines-2026-11')
    due = datetime.fromisoformat(item['publish_at'])
    assert item['publish_at'] == '2026-11-01T18:00:00+03:00'
    assert due.weekday() == 6
    assert item['approved'] is True and item['publish_immediately'] is False
    assert not eligible(item, datetime(2026, 10, 28, 18, tzinfo=timezone.utc), True)
    assert not eligible(item, datetime(2026, 11, 1, 14, 59, tzinfo=timezone.utc), True)
    assert eligible(item, datetime(2026, 11, 1, 15, tzinfo=timezone.utc), True)
    assert not eligible(item, datetime(2026, 11, 1, 21, tzinfo=timezone.utc), True)
    article = (root / item['article']).read_text(encoding='utf-8')
    teaser = (root / item['announcement']).read_text(encoding='utf-8').strip()
    assert 1500 <= len(article.split()) <= 2500
    assert 600 <= len(teaser) <= 1000
    assert len(set(re.findall(r'\]\((https://[^)]+)\)', article))) == 9
    assert 'не техническая экспертиза' in article
    assert 'не считаются независимой проверкой' in article
    render_article(article)
