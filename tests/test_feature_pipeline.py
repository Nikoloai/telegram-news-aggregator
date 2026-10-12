from datetime import datetime, timedelta, timezone

import main
from aggregator.models import FeedItem
from aggregator.readers import handle_reader_command, request_key
from aggregator.storage import Storage
from tests.test_pipeline import setup_pipeline
from tests.test_schedule import freeze_moscow_evening

NOW = datetime(2026, 10, 12, 17, tzinfo=timezone.utc)


def tariff(index=1):
    return FeedItem('Test', f'В России увеличили тариф на услугу {index}', f'https://example.org/tariff-{index}',
                    'В России тариф вырос с 100 до 120 рублей в месяц.', NOW)


def test_feature_is_inside_ten_messages_and_used_once_per_day(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    items = [tariff(), FeedItem('Test', 'В Москве увеличили плату за парковку', 'https://example.org/parking',
                               'В Москве плата выросла с 50 до 80 рублей.', NOW)]
    telegram = setup_pipeline(monkeypatch, items)
    args = main.parse_args(['--state', str(tmp_path/'state.db'), '--scheduled-release'])
    first = main.run(args)
    second = main.run(args)
    public = [text for chat, text in telegram.messages if chat == '@channel']
    assert first.sent == 2 and second.sent == 0
    assert sum('Цена решения' in text for text in public) == 1
    assert '240 руб.' in public[0]
    assert 'Предложить материал' in public[0]
    storage = Storage(tmp_path/'state.db')
    assert storage.feature_count_between(*main.publication_day_window('Europe/Moscow', NOW)) == 1
    assert storage.count_published_between(*main.publication_day_window('Europe/Moscow', NOW)) == 2
    storage.close()


def test_feature_dry_run_does_not_send_or_consume_public_quota(monkeypatch, tmp_path, capsys):
    freeze_moscow_evening(monkeypatch, NOW)
    telegram = setup_pipeline(monkeypatch, [tariff()])
    result = main.run(main.parse_args(['--state', str(tmp_path/'dry.db'), '--author-feature', 'cost', '--dry-run']))
    assert result.sent == 0 and not telegram.messages
    assert 'Цена решения' in capsys.readouterr().out
    storage = Storage(tmp_path/'dry.db')
    assert storage.feature_count_between(*main.publication_day_window('Europe/Moscow', NOW)) == 0
    storage.close()


def test_request_matches_only_independent_feed_and_never_uses_private_text(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    source = {'name': 'The Insider', 'homepage': 'https://theins.ru/', 'enabled': True}
    path = tmp_path/'state.db'
    storage = Storage(path)
    handle_reader_command(storage, '/suggest https://theins.ru/news/987?private=SECRET вопрос PRIVATE',
                          456, [source], 'test-only', NOW)
    storage.close()
    item = FeedItem('The Insider', 'В России повысили налог', 'https://theins.ru/news/987?utm_source=feed',
                    'В России повысили налог на автомобили.', NOW)
    telegram = setup_pipeline(monkeypatch, [item])
    monkeypatch.setattr(main, 'load_sources', lambda path: [source])
    result = main.run(main.parse_args(['--state', str(path), '--author-feature', 'reader']))
    public = [text for chat, text in telegram.messages if chat == '@channel']
    assert result.sent == 1 and 'Разбор по запросу' in public[0]
    assert 'PRIVATE' not in public[0] and 'SECRET' not in public[0]
    storage = Storage(path)
    assert not storage.pending_reader_requests()
    row = storage.connection.execute('SELECT * FROM reader_requests').fetchone()
    assert row['public_url'] == 'https://theins.ru/news/987'
    storage.close()


def test_request_not_in_feed_is_not_fetched(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    path = tmp_path/'state.db'
    storage = Storage(path)
    handle_reader_command(storage, '/suggest https://theins.ru/news/private', 456,
                          [{'name': 'The Insider', 'homepage': 'https://theins.ru/'}], 'test-only', NOW)
    storage.close()
    telegram = setup_pipeline(monkeypatch, [])
    monkeypatch.setattr(main.ArticleFetcher, 'fetch', lambda *args: (_ for _ in ()).throw(AssertionError('Arbitrary fetch')))
    result = main.run(main.parse_args(['--state', str(path), '--author-feature', 'reader']))
    assert result.sent == 0 and not any(chat == '@channel' for chat, _ in telegram.messages)


def seed_watch(path, now):
    storage = Storage(path)
    item = FeedItem('Test', 'В Самаре обещали открыть школу №7', 'https://example.org/old')
    storage.save(item, 'PUBLISHED', topics=['state_policy'], telegram_message_id=50)
    started = now-timedelta(days=8)
    storage.connection.execute('UPDATE articles SET updated_at=?', (started.isoformat(),))
    storage.connection.execute('UPDATE publications SET published_at=?', (started.isoformat(),))
    storage.connection.commit()
    storage.watch_story(storage.recent_published()[0])
    storage.close()


def test_no_update_followup_is_once_and_not_issued_for_empty_collection(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    path = tmp_path/'state.db'
    seed_watch(path, NOW)
    telegram = setup_pipeline(monkeypatch, [])
    args = main.parse_args(['--state', str(path), '--author-feature', 'followup', '--max-items', '1'])
    assert main.run(args).sent == 0
    monkeypatch.setattr(main.FeedCollector, 'collect_all', lambda *args: [tariff()])
    assert main.run(args).sent == 1
    assert main.run(args).sent == 1  # Ordinary, unrelated news can still fill the quota.
    public = [text for chat, text in telegram.messages if chat == '@channel']
    assert sum('Не забыли' in text for text in public) == 1
    assert 'не доказывает, что проблема не решена' in public[0]
    storage = Storage(path)
    assert not storage.due_watches(NOW)
    storage.close()


def test_followup_send_failure_does_not_advance_checkpoint(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    path = tmp_path/'state.db'
    seed_watch(path, NOW)
    telegram = setup_pipeline(monkeypatch, [tariff()])
    telegram.send = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('Test failure'))
    main.run(main.parse_args(['--state', str(path), '--author-feature', 'followup', '--max-items', '1']))
    storage = Storage(path)
    assert storage.due_watches(NOW)[0]['checkpoint'] == 7
    storage.close()


def test_urgent_news_keeps_emergency_rubric_instead_of_cost(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    item = FeedItem('Test', 'В Москве после взрыва началась эвакуация', 'https://example.org/urgent',
                    'В Москве плата выросла с 100 до 120 рублей.', NOW)
    telegram = setup_pipeline(monkeypatch, [item])
    main.run(main.parse_args(['--state', str(tmp_path/'state.db'), '--scheduled-release']))
    public = [text for chat, text in telegram.messages if chat == '@channel']
    assert 'СРОЧНО' in public[0] and 'Цена решения' not in public[0]


def test_daily_limit_blocks_author_rubric_too(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    path = tmp_path/'state.db'
    storage = Storage(path)
    storage.save(FeedItem('Test', 'Старый пост', 'https://example.org/published'), 'PUBLISHED', telegram_message_id=10)
    storage.close()
    monkeypatch.setenv('DAILY_POST_LIMIT', '1')
    telegram = setup_pipeline(monkeypatch, [tariff()])
    result = main.run(main.parse_args(['--state', str(path), '--author-feature', 'cost']))
    assert result.sent == 0 and not telegram.messages


def test_responsibility_feature_reaches_channel_with_exact_roles(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    text = ('Заказчиком проекта названа администрация Москвы.\n'
            'Стоимость контракта составила 120 млн рублей.\n'
            'Строительство больницы не завершено в срок.')
    item = FeedItem('Test', 'В Москве расследуют срыв строительства больницы', 'https://example.org/building', text, NOW)
    telegram = setup_pipeline(monkeypatch, [item])
    result = main.run(main.parse_args(['--state', str(tmp_path/'state.db'), '--author-feature', 'responsibility']))
    public = [post for chat, post in telegram.messages if chat == '@channel']
    assert result.sent == 1 and 'Кто отвечал' in public[0]
    assert 'администрация Москвы' in public[0] and '120 млн рублей' in public[0]
    assert 'не доказывает вину' in public[0]


def test_contrast_feature_uses_two_sources_and_no_unrelated_pair(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    path = tmp_path/'state.db'
    storage = Storage(path)
    storage.save(FeedItem('Earlier', 'Мэр Москвы рассказал о цифровых услугах', 'https://example.org/statement',
                          published_at=NOW), 'PUBLISHED',
                 'В Москве мэр заявил, что сервис «Сфера» полностью готов к запуску.',
                 topics=['state_policy'], telegram_message_id=50)
    storage.close()
    item = FeedItem('Test', 'Минцифры России сообщило о сбое сервиса «Сфера» в Москве',
                    'https://example.org/failure', 'В Москве сервис «Сфера» не работает из-за сбоя.', NOW)
    telegram = setup_pipeline(monkeypatch, [item])
    result = main.run(main.parse_args(['--state', str(path), '--author-feature', 'contrast']))
    public = [post for chat, post in telegram.messages if chat == '@channel']
    assert result.sent == 1 and 'Две России в один день' in public[0]
    assert 'Earlier' in public[0] and 'не доказывает ложь' in public[0]
    assert 'href="https://example.org/statement"' in public[0]


def test_reader_can_request_cost_angle_without_storing_private_question(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    source = {'name': 'The Insider', 'homepage': 'https://theins.ru/', 'enabled': True}
    path = tmp_path/'state.db'
    storage = Storage(path)
    handle_reader_command(storage, '/suggest https://theins.ru/news/987 цена личное PRIVATE', 456,
                          [source], 'test-only', NOW)
    assert storage.pending_reader_requests()[0]['angle'] == 'cost'
    storage.close()
    item = FeedItem('The Insider', 'В России изменился тариф ЖКХ', 'https://theins.ru/news/987',
                    'В России тариф вырос с 100 до 120 рублей в месяц.', NOW)
    telegram = setup_pipeline(monkeypatch, [item])
    monkeypatch.setattr(main, 'load_sources', lambda path: [source])
    main.run(main.parse_args(['--state', str(path), '--author-feature', 'reader']))
    public = [post for chat, post in telegram.messages if chat == '@channel']
    assert 'Разбор по запросу' in public[0] and '240 руб.' in public[0]
    assert 'PRIVATE' not in public[0]


def test_followup_with_new_news_counts_one_message_and_marks_both_article_records(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    path = tmp_path/'state.db'
    seed_watch(path, NOW)
    item = FeedItem('Test', 'В Самаре школу №7 открыли после ремонта', 'https://example.org/opened',
                    'В Самаре школу №7 открыли после завершения ремонта.', NOW)
    telegram = setup_pipeline(monkeypatch, [item])
    result = main.run(main.parse_args(['--state', str(path), '--author-feature', 'followup', '--max-items', '1']))
    public = [post for chat, post in telegram.messages if chat == '@channel']
    assert result.sent == 1 and 'Новое сообщение' in public[0]
    storage = Storage(path)
    window = main.publication_day_window('Europe/Moscow', NOW)
    assert storage.count_published_between(*window) == 1
    assert storage.feature_count_between(*window) == 1
    assert not storage.due_watches(NOW)
    assert storage.find_exact(item.url, '')
    storage.close()
