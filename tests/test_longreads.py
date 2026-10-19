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


def request_revision(root, item, revision=2):
    item['revision'] = revision
    (root / 'queue.json').write_text(json.dumps([item]), encoding='utf-8')
    (root / 'text.md').write_text('# Новый заголовок\n\nНовая редакция с сохранёнными фактами.', encoding='utf-8')


def test_revision_edits_same_targets_without_new_post_or_quota(queue, storage, monkeypatch):
    root, item = queue
    fake_remote(monkeypatch)
    publisher = LongreadPublisher(storage, 'test-bot-secret', '@GVOZDIchKAAA')
    original = publish_due(root, storage, publisher, NOW)
    counters = list(storage.connection.execute('SELECT * FROM publications').fetchall())
    last = storage.last_publication_at()
    request_revision(root, item)
    calls = []
    def edit(url, payload):
        calls.append((url, payload))
        assert 'create' not in url and 'sendMessage' not in url
        return {'path': original['path']} if url.endswith('editPage') else {'message_id': 812}
    monkeypatch.setattr('aggregator.longreads.safe_post', edit)
    # A text edit is not a publication and does not consume even a full daily quota.
    result = publish_due(root, storage, publisher, NOW + timedelta(minutes=1), daily_limit=1)
    assert result['status'] == 'updated'
    assert result['url'] == original['url'] and result['message_id'] == 812
    assert result['published_at'] == original['published_at']
    assert calls[0][1]['path'] == original['path']
    assert calls[1][1]['message_id'] == 812
    assert calls[1][1]['chat_id'] == '@GVOZDIchKAAA'
    assert list(storage.connection.execute('SELECT * FROM publications').fetchall()) == counters
    assert storage.last_publication_at() == last
    stored = json.loads(storage.get_meta('longread:sample'))
    assert stored['status'] == 'published' and stored['revision'] == 2
    row = storage.connection.execute('SELECT title, content FROM articles WHERE telegram_message_id=812').fetchone()
    assert row['title'] == 'Новый заголовок' and 'Новая редакция' in row['content']
    assert publish_due(root, storage, publisher, NOW + timedelta(hours=1)) is None
    assert len(calls) == 2


def test_edit_preview_and_unapproved_revision_do_not_mutate(queue, storage, monkeypatch):
    root, item = queue
    fake_remote(monkeypatch)
    publisher = LongreadPublisher(storage, 'test-bot-secret', '@GVOZDIchKAAA')
    publish_due(root, storage, publisher, NOW)
    request_revision(root, item)
    before = storage.get_meta('longread:sample')
    monkeypatch.setattr('aggregator.longreads.safe_post', lambda *_: pytest.fail('Preview must not write'))
    assert publish_due(root, storage, None, NOW)['status'] == 'preview-update'
    assert storage.get_meta('longread:sample') == before
    item['approved'] = False
    (root / 'queue.json').write_text(json.dumps([item]), encoding='utf-8')
    assert publish_due(root, storage, publisher, NOW) is None
    with pytest.raises(ValueError):
        publisher.refresh(item, root, NOW)


def test_uncertain_edit_can_retry_without_resending(queue, storage, monkeypatch):
    root, item = queue
    fake_remote(monkeypatch)
    publisher = LongreadPublisher(storage, 'test-bot-secret', '@GVOZDIchKAAA')
    publish_due(root, storage, publisher, NOW)
    request_revision(root, item)
    failed = True
    calls = []
    def edit(url, payload):
        nonlocal failed
        calls.append(url)
        assert url.endswith(('editPage', 'editMessageText'))
        if url.endswith('editPage'):
            return {'path': 'Article-10-21'}
        if failed:
            failed = False
            raise RemoteError('Uncertain edit response')
        return {'message_id': 812}
    monkeypatch.setattr('aggregator.longreads.safe_post', edit)
    with pytest.raises(RemoteError):
        publish_due(root, storage, publisher, NOW)
    assert json.loads(storage.get_meta('longread:sample'))['status'] == 'published'
    assert publish_due(root, storage, publisher, NOW)['status'] == 'updated'
    assert len(calls) == 4
    assert storage.connection.execute('SELECT COUNT(*) FROM publications').fetchone()[0] == 1


def test_refresh_never_creates_missing_publication_or_account(queue, storage, monkeypatch):
    root, item = queue
    calls = fake_remote(monkeypatch)
    publisher = LongreadPublisher(storage, 'test-bot-secret', '@GVOZDIchKAAA')
    item['revision'] = 2
    with pytest.raises(ValueError):
        publisher.refresh(item, root, NOW)
    assert calls == []


@pytest.mark.parametrize('revision', [0, -1, True, '2', 1.5])
def test_queue_rejects_invalid_revision(queue, revision):
    root, item = queue
    request_revision(root, item, revision)
    with pytest.raises(ValueError):
        load_queue(root)


def test_repeated_telegram_edit_is_success_but_other_errors_are_not(monkeypatch):
    from aggregator.longreads import safe_post
    class Response:
        status_code = 400
        def json(self):
            return {'ok': False, 'error_code': 400,
                    'description': 'Bad Request: message is not modified: specified new message content is the same'}
    monkeypatch.setattr('aggregator.longreads.requests.post', lambda *a, **kw: Response())
    assert safe_post('https://api.telegram.org/botTEST/editMessageText', {'message_id': 812}) == {'message_id': 812}
    with pytest.raises(RemoteError):
        safe_post('https://api.telegram.org/botTEST/sendMessage', {'message_id': 812})
    with pytest.raises(RemoteError):
        safe_post('https://api.telegra.ph/editPage', {'message_id': 812})
