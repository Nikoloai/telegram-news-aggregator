from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
import requests

import main
from aggregator.article import ArticleFetcher
from aggregator.models import Article, FeedItem
from aggregator.page_changes import PageMonitor, UncertainPageSend, changed_fragment
from aggregator.review import process_editor_messages
from aggregator.storage import Storage
from tests.test_pipeline import setup_pipeline
from tests.test_review import FakeTelegram, private_command
from tests.test_schedule import freeze_moscow_evening

NOW = datetime(2026, 11, 11, 10, tzinfo=timezone.utc)
SOURCE = {'name': 'Test', 'homepage': 'https://example.org/', 'enabled': True}
TITLE = 'В Самаре обещали завершить строительство школы'
URL = 'https://example.org/school'
BEFORE = ('Администрация Самары планирует завершить строительство школы до декабря 2026 года.\n'
          'Стоимость контракта составляет 450 млн рублей из регионального бюджета.\n'
          'В новом корпусе предусмотрены учебные кабинеты, библиотека и спортивный зал.')
AFTER = BEFORE.replace('2026', '2027')


def seed(path, now=NOW):
    storage = Storage(path)
    storage.save(FeedItem('Test', TITLE, URL), 'PUBLISHED', BEFORE,
                 topics=['state_policy'], telegram_message_id=50)
    old = (now - timedelta(hours=8)).isoformat()
    storage.connection.execute('UPDATE articles SET updated_at=?', (old,))
    storage.connection.execute('UPDATE publications SET published_at=?', (old,))
    storage.connection.commit()
    monitor = PageMonitor(storage)
    monitor.enroll(storage.recent_published(), [SOURCE], now - timedelta(hours=8))
    monitor.observe(Article(FeedItem('Test', TITLE, URL), BEFORE, page_verified=True), now - timedelta(hours=8))
    return storage, monitor


@pytest.mark.parametrize('current', [
    BEFORE.replace('2026', '2027'),
    BEFORE.replace('450 млн', '550 млн'),
    BEFORE.replace('450 млн', '450 млрд'),
    BEFORE.replace('планирует', 'не планирует'),
])
def test_substantive_change_keeps_exact_original_fragments(current):
    before, after = changed_fragment(BEFORE, current)
    assert before in BEFORE and after in current and before != after


@pytest.mark.parametrize('current', [
    BEFORE.replace('контракта', 'контракта  '),
    BEFORE.replace('450 млн', '450,00 млн'),
    BEFORE.replace('450 млн', '450 миллионов'),
    BEFORE.replace('450 млн', '450 000 000'),
    BEFORE.replace('библиотека', 'библиотечный зал'),
    'Обновлено\n' + AFTER,
    '\n'.join(reversed(BEFORE.splitlines())),
])
def test_cosmetic_edits_explicit_updates_and_reordering_are_silent(current):
    assert changed_fragment(BEFORE, current) is None


def test_removed_promise_has_no_invented_replacement_text():
    before, after = changed_fragment(BEFORE, '\n'.join(BEFORE.splitlines()[1:]))
    assert before == BEFORE.splitlines()[0] and after == ''


def test_failed_or_replaced_page_cannot_overwrite_baseline(tmp_path):
    storage, monitor = seed(tmp_path/'state.db')
    item = FeedItem('Test', TITLE, URL)
    monitor.observe(Article(item, AFTER), NOW)
    unrelated = 'Для доступа к личному кабинету подтвердите вход на сайт. ' * 5
    monitor.observe(Article(item, unrelated, page_verified=True), NOW)
    versions = storage.connection.execute('SELECT * FROM page_versions').fetchall()
    assert len(versions) == 1 and versions[0]['body_text'] == BEFORE
    assert monitor.pending(NOW) is None
    storage.close()


def test_versions_survive_restart_and_report_only_latest_change(tmp_path):
    path = tmp_path/'state.db'
    storage, monitor = seed(path)
    item = FeedItem('Test', TITLE, URL)
    monitor.observe(Article(item, AFTER, page_verified=True), NOW)
    monitor.observe(Article(item, AFTER.replace('2027', '2028'), page_verified=True), NOW + timedelta(minutes=1))
    storage.close()
    storage = Storage(path)
    candidate = PageMonitor(storage).pending(NOW + timedelta(minutes=1))
    assert candidate and '2026' in candidate.body and '2028' in candidate.body
    assert 'Сохранено' in candidate.body and 'Проверено' in candidate.body
    assert candidate.original_message_id == 50
    assert storage.connection.execute("SELECT COUNT(*) FROM page_versions WHERE status='PENDING'").fetchone()[0] == 1
    storage.close()


def test_cosmetic_edit_preserves_unpublished_substantive_change(tmp_path):
    storage, monitor = seed(tmp_path/'state.db')
    item = FeedItem('Test', TITLE, URL)
    monitor.observe(Article(item, AFTER, page_verified=True), NOW)
    monitor.observe(Article(item, AFTER.replace('библиотека', 'библиотечный зал'), page_verified=True),
                    NOW + timedelta(minutes=1))
    candidate = monitor.pending(NOW + timedelta(minutes=1))
    assert candidate and '2026' in candidate.body and '2027' in candidate.body
    storage.close()


def test_explicit_correction_in_actual_page_heading_is_not_a_quiet_edit(tmp_path):
    storage, monitor = seed(tmp_path/'state.db')
    monitor.observe(Article(FeedItem('Test', TITLE, URL), AFTER, page_verified=True), NOW)
    monitor.observe(Article(FeedItem('Test', TITLE, URL), AFTER, page_verified=True,
                            page_title='Уточнение: срок строительства школы'), NOW+timedelta(minutes=1))
    assert monitor.pending(NOW) is None
    storage.close()


def test_reverting_unpublished_change_cancels_candidate(tmp_path):
    storage, monitor = seed(tmp_path/'state.db')
    item = FeedItem('Test', TITLE, URL)
    monitor.observe(Article(item, AFTER, page_verified=True), NOW)
    monitor.observe(Article(item, BEFORE, page_verified=True), NOW + timedelta(minutes=1))
    assert monitor.pending(NOW + timedelta(minutes=1)) is None
    storage.close()


def test_committed_publication_reconciles_interrupted_receipt(tmp_path):
    path = tmp_path/'state.db'
    storage, monitor = seed(path)
    monitor.observe(Article(FeedItem('Test', TITLE, URL), AFTER, page_verified=True), NOW)
    change = monitor.pending(NOW)
    assert monitor.claim(change.item.url)
    storage.save(change.item, 'PUBLISHED', change.evidence, 'ANALYSIS', ['changes'],
                 telegram_message_id=77, rubric='changes')
    storage.close()
    storage = Storage(path)
    assert PageMonitor(storage).pending(NOW) is None
    assert storage.connection.execute('SELECT status FROM page_versions WHERE event_url=?',
                                      (change.item.url,)).fetchone()[0] == 'PUBLISHED'
    storage.close()


def test_concurrent_checks_record_one_revision(tmp_path):
    path = tmp_path/'state.db'
    storage, monitor = seed(path)
    storage.close()
    barrier = Barrier(2)
    def check_page():
        local = Storage(path)
        barrier.wait(timeout=5)
        PageMonitor(local).observe(Article(FeedItem('Test', TITLE, URL), AFTER, page_verified=True), NOW)
        local.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in pool.map(lambda _: check_page(), range(2)):
            assert result is None
    storage = Storage(path)
    assert storage.connection.execute("SELECT COUNT(*) FROM page_versions WHERE status='PENDING'").fetchone()[0] == 1
    assert storage.connection.execute('SELECT COUNT(*) FROM page_versions').fetchone()[0] == 2
    storage.close()


def test_failed_page_check_waits_six_hours_before_retry(tmp_path):
    storage, monitor = seed(tmp_path/'state.db')
    calls = []
    def failed_page(item):
        calls.append(item.url)
        return Article(item, BEFORE)
    fetcher = SimpleNamespace(fetch=failed_page)
    monitor.scan(fetcher, NOW)
    monitor.scan(fetcher, NOW + timedelta(minutes=1))
    monitor.scan(fetcher, NOW + timedelta(hours=6))
    assert calls == [URL, URL]
    assert monitor.pending(NOW + timedelta(hours=6)) is None
    storage.close()


def test_disabling_source_stops_fetching_and_keeps_uncertain_send_evidence(tmp_path):
    storage, monitor = seed(tmp_path/'state.db')
    monitor.observe(Article(FeedItem('Test', TITLE, URL), AFTER, page_verified=True), NOW)
    change = monitor.pending(NOW)
    assert monitor.claim(change.item.url)
    monitor.enroll(storage.recent_published(), [dict(SOURCE, enabled=False)], NOW)
    fetcher = SimpleNamespace(fetch=lambda item: pytest.fail('Disabled source fetched'))
    monitor.scan(fetcher, NOW+timedelta(hours=8))
    assert monitor.pending(NOW+timedelta(hours=8)) is None
    assert storage.connection.execute("SELECT COUNT(*) FROM page_versions WHERE status='SENDING'").fetchone()[0] == 1
    assert storage.connection.execute('SELECT COUNT(*) FROM page_versions').fetchone()[0] == 2
    storage.close()


def test_full_watch_pool_admits_newer_publication(tmp_path):
    storage = Storage(tmp_path/'state.db')
    for index in range(30):
        storage.save(FeedItem('Test', TITLE, f'https://example.org/school-{index}'), 'PUBLISHED', BEFORE,
                     telegram_message_id=index+1)
    monitor = PageMonitor(storage)
    monitor.enroll(storage.recent_published(), [SOURCE], datetime.now(timezone.utc))
    storage.save(FeedItem('Test', TITLE, URL), 'PUBLISHED', BEFORE, telegram_message_id=99)
    monitor.enroll(storage.recent_published(), [SOURCE], datetime.now(timezone.utc))
    assert storage.connection.execute('SELECT COUNT(*) FROM page_watches').fetchone()[0] == 30
    assert storage.connection.execute('SELECT message_id FROM page_watches WHERE url=?', (URL,)).fetchone()[0] == 99
    storage.close()


def test_monitor_collects_changes_after_daily_author_quota_is_used(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    path = tmp_path/'state.db'
    storage, monitor = seed(path)
    storage.save(FeedItem('Test', 'Цена другого решения', 'https://example.org/other'), 'PUBLISHED',
                 telegram_message_id=51, rubric='cost')
    storage.connection.execute('UPDATE publications SET published_at=? WHERE telegram_message_id=51',
                               ((NOW-timedelta(hours=2)).isoformat(),))
    storage.connection.commit()
    storage.close()
    telegram = setup_pipeline(monkeypatch, [])
    monkeypatch.setattr(main, 'load_sources', lambda path: [SOURCE])
    monkeypatch.setattr(main.ArticleFetcher, 'fetch', lambda self, item: Article(item, AFTER, page_verified=True))
    assert main.run(main.parse_args(['--state', str(path), '--scheduled-release'])).sent == 0
    assert not any(channel == '@channel' for channel, _ in telegram.messages)
    storage = Storage(path)
    assert PageMonitor(storage).pending(NOW) is not None
    storage.close()


def test_same_url_unchanged_rss_reaches_channel_once(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    path = tmp_path/'state.db'
    storage, monitor = seed(path)
    storage.close()
    item = FeedItem('Test', TITLE, URL, BEFORE, NOW)
    telegram = setup_pipeline(monkeypatch, [item])
    monkeypatch.setattr(main, 'load_sources', lambda path: [SOURCE])
    monkeypatch.setattr(main.ArticleFetcher, 'fetch', lambda self, item: Article(item, AFTER, page_verified=True))
    args = main.parse_args(['--state', str(path), '--scheduled-release'])
    assert main.run(args).sent == 1
    assert main.run(args).sent == 0
    posts = [body for channel, body in telegram.messages if channel == '@channel']
    assert len(posts) == 1 and 'Тихая правка' in posts[0]
    assert '2026' in posts[0] and '2027' in posts[0] and 'href="https://t.me/channel/50"' in posts[0]
    storage = Storage(path)
    assert storage.find_by_normalized_url(URL)['content'] == BEFORE
    assert storage.feature_count_between(*main.publication_day_window('Europe/Moscow', NOW)) == 1
    storage.close()


def test_first_real_publication_creates_baseline_for_next_check(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    path = tmp_path/'state.db'
    item = FeedItem('Test', TITLE, URL, BEFORE, NOW)
    setup_pipeline(monkeypatch, [item])
    monkeypatch.setattr(main, 'load_sources', lambda path: [SOURCE])
    monkeypatch.setattr(main.ArticleFetcher, 'fetch', lambda self, item: Article(item, BEFORE, page_verified=True))
    assert main.run(main.parse_args(['--state', str(path), '--max-items', '1'])).sent == 1
    storage = Storage(path)
    rows = storage.connection.execute('SELECT * FROM page_versions').fetchall()
    assert len(rows) == 1 and rows[0]['status'] == 'BASELINE' and rows[0]['body_text'] == BEFORE
    storage.close()


def test_dry_run_reads_candidate_without_changing_or_sending(monkeypatch, tmp_path, capsys):
    freeze_moscow_evening(monkeypatch, NOW)
    path = tmp_path/'state.db'
    storage, monitor = seed(path)
    monitor.observe(Article(FeedItem('Test', TITLE, URL), AFTER, page_verified=True), NOW)
    rows_before = [tuple(row) for row in storage.connection.execute('SELECT * FROM page_versions')]
    storage.close()
    telegram = setup_pipeline(monkeypatch, [])
    result = main.run(main.parse_args(['--state', str(path), '--author-feature', 'changes', '--dry-run']))
    assert result.sent == 0 and not telegram.messages and 'Тихая правка' in capsys.readouterr().out
    storage = Storage(path)
    assert [tuple(row) for row in storage.connection.execute('SELECT * FROM page_versions')] == rows_before
    storage.close()


def test_uncertain_send_is_quarantined_and_does_not_continue_current_run(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    path = tmp_path/'state.db'
    storage, monitor = seed(path)
    monitor.observe(Article(FeedItem('Test', TITLE, URL), AFTER, page_verified=True), NOW)
    storage.close()
    ordinary = FeedItem('Test', 'Госдума России повысила налог на автомобили',
                        'https://example.org/tax', published_at=NOW)
    telegram = setup_pipeline(monkeypatch, [ordinary])
    monkeypatch.setattr(main, 'load_sources', lambda path: [SOURCE])
    attempts = []
    def uncertain_send(*args, **kwargs):
        attempts.append(args)
        raise requests.Timeout('An accepted response may have been lost')
    telegram.send = uncertain_send
    args = main.parse_args(['--state', str(path), '--author-feature', 'changes'])
    assert main.run(args).sent == 0
    monkeypatch.setattr(main.FeedCollector, 'collect_all', lambda *args: [])
    assert main.run(args).sent == 0
    assert len(attempts) == 1
    storage = Storage(path)
    assert storage.connection.execute('SELECT status FROM page_versions ORDER BY id DESC LIMIT 1').fetchone()[0] == 'SENDING'
    storage.close()


@pytest.mark.parametrize('when,limit', [(NOW.replace(hour=20), 10), (NOW, 1)])
def test_night_and_daily_limit_do_not_release_candidate(monkeypatch, tmp_path, when, limit):
    freeze_moscow_evening(monkeypatch, when)
    path = tmp_path/'state.db'
    storage, monitor = seed(path, when)
    monitor.observe(Article(FeedItem('Test', TITLE, URL), AFTER, page_verified=True), when)
    storage.close()
    telegram = setup_pipeline(monkeypatch, [])
    monkeypatch.setenv('DAILY_POST_LIMIT', str(limit))
    assert main.run(main.parse_args(['--state', str(path), '--author-feature', 'changes'])).sent == 0
    assert not telegram.messages


def test_editor_uncertain_send_cannot_repeat_same_revision(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    monkeypatch.setattr('aggregator.review.datetime', main.datetime)
    storage, monitor = seed(tmp_path/'state.db')
    monitor.observe(Article(FeedItem('Test', TITLE, URL), AFTER, page_verified=True), NOW)
    change = monitor.pending(NOW)
    review_id = storage.enqueue_review(change.item, change.body, change.evidence, 'ANALYSIS', ['changes'],
                                       ['Manual check'], rubric='changes', body_text=change.body)
    monitor.mark(change, 'REVIEW_PENDING')
    telegram = FakeTelegram([private_command(1, 123, f'/publish {review_id}')])
    attempts = []
    def uncertain_send(chat, *args, **kwargs):
        if chat == '@channel':
            attempts.append(chat)
            raise requests.Timeout()
        return 99
    telegram.send = uncertain_send
    start, end = main.publication_day_window('Europe/Moscow', NOW)
    with pytest.raises(UncertainPageSend):
        process_editor_messages(storage, telegram, '123', '@channel', start, end, 10, True)
    assert process_editor_messages(storage, telegram, '123', '@channel', start, end, 10, True) == 0
    assert len(attempts) == 1
    storage.close()


@pytest.mark.parametrize('kind', ['failure', 'truncated', 'redirect', 'valid'])
def test_fetcher_marks_only_successful_complete_same_page_as_verified(monkeypatch, kind):
    fetcher = ArticleFetcher(max_chars=500)
    markup = '<article><p>' + BEFORE.replace('\n', '</p><p>') + '</p></article>'
    response = SimpleNamespace(text=markup, headers={'Content-Type': 'text/html'}, url=URL,
                               raise_for_status=lambda: None)
    if kind == 'redirect':
        response.url = 'https://example.org/login'
    elif kind == 'truncated':
        response.text = markup.replace('</article>', '<p>' + 'Long paragraph ' * 60 + '</p></article>')
    def get(*args, **kwargs):
        if kind == 'failure':
            raise requests.Timeout()
        return response
    monkeypatch.setattr(fetcher.session, 'get', get)
    article = fetcher.fetch(FeedItem('Test', TITLE, URL, AFTER))
    assert article.page_verified is (kind == 'valid')


def test_html_reformatting_does_not_look_like_a_removed_promise(monkeypatch, tmp_path):
    storage = Storage(tmp_path/'state.db')
    storage.save(FeedItem('Test', TITLE, URL), 'PUBLISHED', BEFORE, telegram_message_id=50)
    monitor = PageMonitor(storage)
    monitor.enroll(storage.recent_published(), [SOURCE], datetime.now(timezone.utc))
    fetcher = ArticleFetcher()
    markup = '<article><p>' + BEFORE.replace('\n', '</p><p>') + '</p></article>'
    response = SimpleNamespace(text=markup, headers={'Content-Type': 'text/html'}, url=URL,
                               raise_for_status=lambda: None)
    monkeypatch.setattr(fetcher.session, 'get', lambda *args, **kwargs: response)
    first = datetime.now(timezone.utc)
    monitor.observe(fetcher.fetch(FeedItem('Test', TITLE, URL)), first)
    response.text = markup.replace('<p>', '<h2>', 1).replace('</p>', '</h2>', 1)
    monitor.observe(fetcher.fetch(FeedItem('Test', TITLE, URL)), first+timedelta(hours=6))
    assert monitor.pending(first+timedelta(hours=6)) is None
    assert storage.connection.execute('SELECT COUNT(*) FROM page_versions').fetchone()[0] == 1
    storage.close()
