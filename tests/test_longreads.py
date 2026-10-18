import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aggregator.longreads import (LongreadPublisher, RemoteError, announcement_html,
                                 eligible, load_queue, publish_due, render_article)
from aggregator.storage import Storage

NOW = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def queue(tmp_path):
    (tmp_path / 'text.md').write_text('# Проверка <заголовка>\n\nТекст [источник](https://example.org/a).\n\n## Глава\n\n**Важно** и *оговорка*.', encoding='utf-8')
    (tmp_path / 'teaser.txt').write_text('Проверенный анонс материала. ' * 8, encoding='utf-8')
    item = {'slug': 'sample', 'article': 'text.md', 'announcement': 'teaser.txt',
            'publish_at': NOW.isoformat(), 'approved': True}
    (tmp_path / 'queue.json').write_text(json.dumps([item]), encoding='utf-8')
    return tmp_path, item


@pytest.fixture
def storage(tmp_path):
    store = Storage(tmp_path / 'state.db')
    yield store
    store.close()


def fake_remote(monkeypatch, fail_send=False):
    calls = []
    def post(url, payload):
        calls.append((url, payload))
        if url.endswith('createAccount'):
            return {'access_token': 'private-telegraph-key'}
        if url.endswith('createPage'):
            return {'url': 'https://telegra.ph/Article-10-21', 'path': 'Article-10-21'}
        if fail_send:
            raise RemoteError('uncertain')
        return {'message_id': 812}
    monkeypatch.setattr('aggregator.longreads.safe_post', post)
    return calls


def test_renderer_preserves_source_links_and_formatting():
    title, nodes = render_article('# Title\n\nText [source](https://example.org/a).\n\n## Heading\n\n**Bold** and *italic*.')
    assert title == 'Title'
    assert nodes[0]['children'][1]['attrs']['href'] == 'https://example.org/a'
    assert nodes[1]['tag'] == 'h3'
    assert nodes[2]['children'][0]['tag'] == 'strong'
    assert nodes[2]['children'][2]['tag'] == 'em'


def test_renderer_rejects_oversized_content():
    with pytest.raises(ValueError):
        render_article('# Title\n\n' + 'а' * 64000)


def test_announcement_escapes_html_and_requires_telegraph():
    assert '&lt;Title&gt;' in announcement_html('<Title>', '<teaser>', 'https://telegra.ph/Test-01-01')
    with pytest.raises(ValueError):
        announcement_html('Title', 'Text', 'https://evil.example/a')


def test_queue_paths_cannot_escape_content_root(queue):
    root, item = queue
    item['article'] = '../not-allowed.md'
    (root / 'queue.json').write_text(json.dumps([item]), encoding='utf-8')
    with pytest.raises(ValueError):
        load_queue(root)


def test_unapproved_or_future_article_is_not_eligible(queue):
    _, item = queue
    assert not eligible(item, NOW - timedelta(seconds=1))
    assert not eligible(dict(item, approved=False), NOW)


def test_nights_are_only_overridden_for_explicit_immediate_entry(queue):
    _, item = queue
    night = NOW.replace(hour=23)
    assert not eligible(item, night, True)
    assert eligible(dict(item, publish_immediately=True), night, True)
    assert not eligible(dict(item, publish_immediately=True), night)


def test_stale_scheduled_article_is_not_dumped(queue):
    _, item = queue
    assert not eligible(item, NOW + timedelta(days=3))


def test_dry_preview_has_no_external_writes_or_state_changes(queue, storage):
    root, _ = queue
    assert publish_due(root, storage, None, NOW)['status'] == 'preview'
    assert storage.get_meta('longread:sample') is None


def test_publish_once_encrypted_account_and_shared_quota(queue, storage, monkeypatch):
    root, _ = queue
    calls = fake_remote(monkeypatch)
    publisher = LongreadPublisher(storage, 'test-bot-secret', '@GVOZDIchKAAA')
    result = publish_due(root, storage, publisher, NOW)
    assert result['message_id'] == 812
    assert storage.rubric_count_between('longread', NOW - timedelta(days=1), NOW + timedelta(days=1)) == 1
    assert 'private-telegraph-key' not in storage.get_meta('longreads:telegraph:encrypted_token')
    assert publisher.account_token() == 'private-telegraph-key'
    assert publish_due(root, storage, publisher, NOW + timedelta(hours=2)) is None
    assert len(calls) == 3


def test_uncertain_send_is_not_retried_and_does_not_block_pipeline(queue, storage, monkeypatch):
    root, _ = queue
    calls = fake_remote(monkeypatch, fail_send=True)
    publisher = LongreadPublisher(storage, 'test-bot-secret', '@GVOZDIchKAAA')
    with pytest.raises(RemoteError):
        publish_due(root, storage, publisher, NOW)
    assert json.loads(storage.get_meta('longread:sample'))['status'] == 'sending'
    assert publish_due(root, storage, publisher, NOW + timedelta(hours=1)) is None
    assert len(calls) == 3


def test_pause_and_daily_limit_apply_before_external_writes(queue, storage, monkeypatch):
    root, _ = queue
    calls = fake_remote(monkeypatch)
    publisher = LongreadPublisher(storage, 'test-bot-secret', '@GVOZDIchKAAA')
    storage.connection.execute('INSERT INTO publications VALUES (?,?,?,?,?)',
                               ('other', 'https://example.org', (NOW - timedelta(minutes=30)).isoformat(), 'news', 1))
    storage.connection.commit()
    assert publish_due(root, storage, publisher, NOW) is None
    assert publish_due(root, storage, publisher, NOW + timedelta(hours=1), daily_limit=1) is None
    assert calls == []


def test_rotated_bot_key_does_not_silently_discard_account(storage, monkeypatch):
    fake_remote(monkeypatch)
    LongreadPublisher(storage, 'old-key', '@GVOZDIchKAAA').account_token()
    with pytest.raises(RemoteError):
        LongreadPublisher(storage, 'new-key', '@GVOZDIchKAAA').account_token()


def test_actual_approved_manuscripts_are_valid_and_under_telegraph_limit():
    items = load_queue(Path('content/longreads'))
    assert [item['slug'] for item in items[:2]] == ['max-2026-10', 'bucha-2026-10']
    bucha = items[1]
    assert not eligible(bucha, datetime(2026, 10, 20, 15, tzinfo=timezone.utc), True)
    assert eligible(bucha, NOW)
