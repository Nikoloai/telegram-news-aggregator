from datetime import datetime, timedelta, timezone

from aggregator.readers import handle_reader_command, reader_key, request_key, source_url
from aggregator.review import process_editor_messages
from aggregator.storage import Storage
from tests.test_review import FakeTelegram, private_command

SOURCES = [{'name': 'The Insider', 'homepage': 'https://theins.ru/', 'enabled': True}]
NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
KEY = 'test-secret-not-a-real-token'


def test_only_one_https_public_source_link_is_accepted():
    assert source_url('https://theins.ru/news/123?email=private#secret', SOURCES) == ('https://theins.ru/news/123', 'The Insider')
    for url in ['http://theins.ru/news/123', 'https://theins.ru.evil.org/news/123',
                'https://127.0.0.1/a', 'https://localhost/a', 'file:///secret',
                'https://theins.ru:8080/news/123', 'https://user:secret@theins.ru/news/123',
                'https://t.me/+private', 'https://theins.ru/news/1 https://theins.ru/news/2']:
        assert source_url(url, SOURCES) is None


def test_private_text_ids_and_unverified_url_are_not_in_persistent_state(tmp_path):
    path = tmp_path/'state.db'
    storage = Storage(path)
    url = 'https://theins.ru/news/my_private_path?phone=secret'
    reply = handle_reader_command(storage, '/suggest '+url+' вопрос SECRET PRIVATE EMAIL', 987654321, SOURCES, KEY, NOW)
    assert 'принят' in reply
    row = storage.pending_reader_requests()[0]
    assert row['url'] == request_key(KEY, 'https://theins.ru/news/my_private_path')
    assert row['public_url'] is None
    serialized = '\n'.join(storage.connection.iterdump())
    assert '987654321' not in serialized
    assert 'my_private_path' not in serialized
    assert 'SECRET PRIVATE EMAIL' not in serialized
    assert KEY not in serialized
    storage.close()


def test_duplicate_and_rate_limit_and_forget(tmp_path):
    storage = Storage(tmp_path/'state.db')
    command = '/suggest https://theins.ru/news/1'
    assert 'принят' in handle_reader_command(storage, command, 123, SOURCES, KEY, NOW)
    assert 'Повтор' in handle_reader_command(storage, command, 123, SOURCES, KEY, NOW)
    for index in [2, 3]:
        assert 'принят' in handle_reader_command(storage, f'/suggest https://theins.ru/news/{index}', 123, SOURCES, KEY, NOW)
    assert 'Лимит' in handle_reader_command(storage, '/suggest https://theins.ru/news/4', 123, SOURCES, KEY, NOW)
    assert 'принят' in handle_reader_command(storage, '/suggest https://theins.ru/news/4', 123, SOURCES, KEY, NOW+timedelta(days=1, seconds=1))
    assert 'удалён' in handle_reader_command(storage, '/forget', 123, SOURCES, KEY, NOW)
    assert not storage.pending_reader_requests()
    storage.close()


def test_unverified_request_is_never_fetched_or_published_by_command(tmp_path):
    storage = Storage(tmp_path/'state.db')
    telegram = FakeTelegram([private_command(1, 999, '/suggest https://theins.ru/news/123')])
    process_editor_messages(storage, telegram, '123', '@channel', NOW, NOW+timedelta(days=1), 10, True,
                            sources=SOURCES, privacy_key=KEY)
    assert storage.pending_reader_requests()
    assert all(chat == '999' for chat, _ in telegram.messages)
    assert storage.count_published_between(NOW, NOW+timedelta(days=1)) == 0
    storage.close()


def test_private_help_does_not_grant_editor_access_or_disclose_editor(tmp_path):
    storage = Storage(tmp_path/'state.db')
    telegram = FakeTelegram([private_command(1, 999, '/start request'), private_command(2, 999, '/requests')])
    process_editor_messages(storage, telegram, '123', '@channel', NOW, NOW+timedelta(days=1), 10, True,
                            sources=SOURCES, privacy_key=KEY)
    assert len(telegram.messages) == 1
    assert '/suggest' in telegram.messages[0][1]
    assert '123' not in telegram.messages[0][1]
    storage.close()


def test_group_and_bot_suggestions_are_ignored(tmp_path):
    storage = Storage(tmp_path/'state.db')
    group = private_command(1, 999, '/suggest https://theins.ru/news/123')
    group['message']['chat']['type'] = 'group'
    robot = private_command(2, 999, '/suggest https://theins.ru/news/124')
    robot['message']['from']['is_bot'] = True
    telegram = FakeTelegram([group, robot])
    process_editor_messages(storage, telegram, '123', '@channel', NOW, NOW+timedelta(days=1), 10, True,
                            sources=SOURCES, privacy_key=KEY)
    assert not telegram.messages and not storage.pending_reader_requests()
    storage.close()


def test_requests_expire_after_thirty_days(tmp_path):
    storage = Storage(tmp_path/'state.db')
    handle_reader_command(storage, '/suggest https://theins.ru/news/1', 1, SOURCES, KEY, NOW)
    handle_reader_command(storage, '/suggest https://theins.ru/news/2', 2, SOURCES, KEY, NOW+timedelta(days=31))
    assert len(storage.pending_reader_requests()) == 1
    assert len(list(storage.connection.execute('SELECT * FROM reader_votes'))) == 1
    storage.close()
