from datetime import datetime, timedelta, timezone

import pytest

import main
from aggregator.features import FEATURE_RUBRICS
from aggregator.models import Article, FeedItem
from aggregator.satire import build_translation, build_excuse, build_career, select_satire_feature, satire_week_window
from aggregator.storage import Storage
from aggregator.review import process_editor_messages
from aggregator.readers import handle_reader_command
from rewriter.llm import LLMRewriter
from tests.test_pipeline import setup_pipeline
from tests.test_review import FakeTelegram, private_command
from tests.test_schedule import freeze_moscow_evening


NOW = datetime(2026, 10, 13, 16, 30, tzinfo=timezone.utc)
ZONE = timezone(timedelta(hours=3))
TRANSLATION = 'Министр Иван Петров назвал сокращение расходов «повышением эффективности управления».'
EXCUSE = ('Сервис «Сфера» в России не заработал к сроку.\n'
          'Министр Иван Петров объяснил сбой сервиса «Сфера»: «Это временные технические трудности».' )
CAREER = ('В 2021 году дипломата Александра Красия выслали из Болгарии.\n'
          'В 2026 году дипломат Александр Красий работает в посольстве России в Сербии.')
COMMENTARY = 'Платить предлагают живыми деньгами. Радоваться, видимо, придётся воображаемыми.'


def article(text, title='В России чиновник объяснил решение', url='https://example.org/news'):
    return Article(FeedItem('Test', title, url, text, NOW), text)


def test_translation_requires_actual_attributed_official_quote():
    draft = build_translation(article(TRANSLATION))
    assert draft.rubric == 'translation' and TRANSLATION in draft.body
    assert draft.evidence == (TRANSLATION,)
    for text in ('Министр назвал сокращение расходов оптимизацией.',
                 'Журналист назвал решение «повышением эффективности управления».',
                 'Журналист сказал: «Министр объявил повышение эффективности управления».',
                 'Министр сказал: «Открытие сервиса состоится в среду».'):
        assert build_translation(article(text)) is None


def test_excuse_uses_real_explanation_and_connected_failure():
    draft = build_excuse(article(EXCUSE))
    assert draft.rubric == 'excuse' and 'не заработал' in draft.body
    assert all(value in EXCUSE for value in draft.evidence)
    assert 'худшее объяснение страны' not in draft.body
    assert build_excuse(article(TRANSLATION)) is None
    assert build_excuse(article(EXCUSE.replace('Сервис «Сфера» в России не заработал к сроку.',
                                               'Сервис «Облако» в России не заработал к сроку.'))) is None
    assert build_excuse(article('Сервис не заработал к сроку.\nМинистр назвал ремонт «плановыми работами ведомства».')) is None


def test_excuse_does_not_duplicate_a_single_evidence_sentence():
    text = 'Министр Иван Петров объяснил, почему сервис не заработал в 2026 году: «Внешние объективные обстоятельства».'
    draft = build_excuse(article(text))
    assert draft and draft.body.count(text) == 1


def test_career_keeps_identity_dates_and_no_reward_claim():
    draft = build_career(article(CAREER, 'Российский дипломат работает в новом посольстве'))
    assert draft.rubric == 'career'
    assert all(value in CAREER for value in draft.evidence)
    assert '2021' in draft.body and '2026' in draft.body
    assert 'не доказывает' in draft.body
    assert build_career(article(CAREER.replace('Александр Красий работает', 'Александр Иванов работает'))) is None
    assert build_career(article(CAREER.replace('2026 году', '2020 году'))) is None
    assert build_career(article(CAREER.replace('работает в посольстве', 'может работать в посольстве'))) is None
    assert build_career(article('Ранее чиновника выслали из страны. Теперь он работает в посольстве.')) is None


def test_career_handles_russian_case_without_matching_two_different_people():
    text = ('Ранее министра Ивана Петрова уволили после срыва проекта.\n'
            'Министр Иван Петров назначен главой ведомства.')
    assert build_career(article(text))
    assert not build_career(article(text.replace('Иван Петров назначен', 'Иван Петренко назначен')))
    assert not build_career(article(text.replace('Ивана Петрова', 'Владимира Петрова')
                                   .replace('Иван Петров', 'Владислав Петров')))


@pytest.mark.parametrize('builder,text', [(build_translation, TRANSLATION), (build_excuse, EXCUSE), (build_career, CAREER)])
def test_new_rubrics_never_joke_about_victims(builder, text):
    assert builder(article(text + '\nВ результате погибли люди.')) is None


def test_week_is_monday_to_monday_in_moscow_not_utc():
    now = datetime(2026, 10, 11, 21, 30, tzinfo=timezone.utc)  # Monday in Moscow.
    start, end = satire_week_window(now, ZONE)
    assert start == datetime(2026, 10, 11, 21, tzinfo=timezone.utc)
    assert end - start == timedelta(days=7)


def test_weekly_budget_and_one_excuse_do_not_force_any_rubric():
    assert select_satire_feature(article(TRANSLATION), 'translation', 2, 0) is None
    assert select_satire_feature(article(EXCUSE), 'excuse', 0, 1) is None
    assert select_satire_feature(article(EXCUSE), 'auto', 0, 0).rubric == 'excuse'
    assert set(('translation', 'excuse', 'career')) <= set(FEATURE_RUBRICS)


class RubricWriter:
    def __init__(self, commentary=COMMENTARY):
        self.commentary = commentary
        self.drafts = []
    def rewrite_commentary(self, article, draft):
        self.drafts.append(draft.rubric)
        return self.commentary
    def rewrite(self, article, mode):
        return article.item.title + '\n\n' + article.text


@pytest.mark.parametrize('rubric,text,label', [
    ('translation', TRANSLATION, 'Перевод с чиновничьего'),
    ('excuse', EXCUSE, 'Отмазка недели'),
    ('career', CAREER, 'Карьерный лифт'),
])
def test_new_rubrics_reach_pipeline_once_and_keep_source_separate(monkeypatch, tmp_path, rubric, text, label):
    freeze_moscow_evening(monkeypatch, NOW)
    telegram = setup_pipeline(monkeypatch, [article(text).item])
    monkeypatch.setenv('EDITORIAL_STYLE', 'satirical')
    writer = RubricWriter()
    monkeypatch.setattr(main, 'choose_rewriter', lambda: writer)
    path = tmp_path/'state.db'
    args = main.parse_args(['--state', str(path), '--author-feature', rubric])
    assert main.run(args).sent == 1 and main.run(args).sent == 0
    public = [body for chat, body in telegram.messages if chat == '@channel']
    assert label in public[0] and COMMENTARY in public[0] and public[0].count(COMMENTARY) == 1
    assert 'Авторский перевод (сатира)' in public[0] if rubric == 'translation' else 'Авторский разбор (сатира)' in public[0]
    storage = Storage(path)
    assert storage.feature_count_between(*main.publication_day_window('Europe/Moscow', NOW)) == 1
    assert storage.satire_count_between(*satire_week_window(NOW, ZONE)) == 1
    assert len(storage.recent_style()) == 1
    storage.close()


def seed_week(path, rubrics):
    storage = Storage(path)
    for index, rubric in enumerate(rubrics):
        storage.save(FeedItem('Test', f'Ранее {index}', f'https://example.org/old-{index}'), 'PUBLISHED',
                     telegram_message_id=50+index, rubric=rubric)
    old = (NOW-timedelta(days=1)).isoformat()
    storage.connection.execute('UPDATE publications SET published_at=?', (old,))
    storage.connection.execute('UPDATE articles SET updated_at=?', (old,))
    storage.connection.commit()
    storage.close()


@pytest.mark.parametrize('rubrics,requested,text', [
    (['career', 'translation'], 'translation', TRANSLATION),
    (['excuse'], 'excuse', EXCUSE),
])
def test_pipeline_respects_weekly_limits_without_blocking_ordinary_news(monkeypatch, tmp_path, rubrics, requested, text):
    freeze_moscow_evening(monkeypatch, NOW)
    path = tmp_path/'state.db'
    seed_week(path, rubrics)
    telegram = setup_pipeline(monkeypatch, [article(text).item])
    writer = RubricWriter()
    monkeypatch.setattr(main, 'choose_rewriter', lambda: writer)
    assert main.run(main.parse_args(['--state', str(path), '--author-feature', requested])).sent == 1
    assert not writer.drafts
    public = [body for chat, body in telegram.messages if chat == '@channel']
    assert 'Авторский перевод' not in public[0] and 'Авторский разбор' not in public[0]


@pytest.mark.parametrize('bad', ['Это обошлось в 999 рублей и объясняется коррупционной схемой чиновников.',
                                 'Министр заявил: «Мы всё сделали прекрасно и так будет дальше».',
                                 'Коротко.', 'x'*321])
def test_malformed_commentary_never_becomes_a_fake_quote_or_feature(monkeypatch, tmp_path, bad):
    freeze_moscow_evening(monkeypatch, NOW)
    telegram = setup_pipeline(monkeypatch, [article(TRANSLATION).item])
    monkeypatch.setattr(main, 'choose_rewriter', lambda: RubricWriter(bad))
    main.run(main.parse_args(['--state', str(tmp_path/'state.db'), '--author-feature', 'translation']))
    public = [body for chat, body in telegram.messages if chat == '@channel']
    assert len(public) == 1 and 'Авторский перевод' not in public[0] and bad not in public[0]


def test_dry_run_does_not_send_consume_weekly_budget_or_remember_a_joke(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    telegram = setup_pipeline(monkeypatch, [article(TRANSLATION).item])
    monkeypatch.setattr(main, 'choose_rewriter', RubricWriter)
    path = tmp_path/'state.db'
    result = main.run(main.parse_args(['--state', str(path), '--author-feature', 'translation', '--dry-run']))
    assert result.previews == 1 and result.sent == 0 and not telegram.messages
    storage = Storage(path)
    assert storage.satire_count_between(*satire_week_window(NOW, ZONE)) == 0 and not storage.recent_style()
    storage.close()


def test_send_failure_does_not_consume_weekly_budget(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    telegram = setup_pipeline(monkeypatch, [article(TRANSLATION).item])
    monkeypatch.setattr(main, 'choose_rewriter', RubricWriter)
    telegram.send = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('Test failure'))
    path = tmp_path/'state.db'
    assert main.run(main.parse_args(['--state', str(path), '--author-feature', 'translation'])).sent == 0
    storage = Storage(path)
    assert not storage.recent_style() and storage.satire_count_between(*satire_week_window(NOW, ZONE)) == 0
    storage.close()


def test_private_approval_respects_weekly_limit(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    monkeypatch.setattr('aggregator.review.datetime', main.datetime)
    path = tmp_path/'state.db'
    seed_week(path, ['career', 'translation'])
    storage = Storage(path)
    item = article(TRANSLATION).item
    row_id = storage.enqueue_review(item, TRANSLATION, TRANSLATION, 'SATIRICAL', ['state_policy'], ['Check'],
                                    rubric='excuse', body_text=TRANSLATION)
    telegram = FakeTelegram([private_command(1, 123, f'/publish {row_id}')])
    day = main.publication_day_window('Europe/Moscow', NOW)
    assert process_editor_messages(storage, telegram, '123', '@channel', *day, 10, True) == 0
    assert storage.get_review(row_id)['status'] == 'PENDING'
    assert any('Две сатирические' in body for _, body in telegram.messages)
    storage.close()


def test_llm_commentary_request_labels_it_as_satire_and_uses_style_memory(monkeypatch):
    requests = []
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {'choices': [{'message': {'content': COMMENTARY}}]}
    monkeypatch.setattr('rewriter.llm.requests.post', lambda *args, **kwargs: requests.append(kwargs) or Response())
    writer = LLMRewriter('openai', 'test-only', 'test-model')
    writer.set_style_memory(['Раньше была такая шутка, больше её не повторять.'])
    value = article(TRANSLATION)
    writer.rewrite_commentary(value, build_translation(value))
    messages = requests[0]['json']['messages']
    assert 'один абзац на 60–320' in messages[0]['content']
    assert 'не слова чиновника' in messages[1]['content']
    assert TRANSLATION in messages[1]['content'] and 'Память стиля' in messages[0]['content']


def test_automatic_scheduled_release_uses_new_rubric_without_new_timer(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    telegram = setup_pipeline(monkeypatch, [article(TRANSLATION).item])
    writer = RubricWriter()
    monkeypatch.setattr(main, 'choose_rewriter', lambda: writer)
    main.run(main.parse_args(['--state', str(tmp_path/'state.db'), '--scheduled-release']))
    assert writer.drafts == ['translation']
    assert any(chat == '@channel' and 'Перевод с чиновничьего' in body for chat, body in telegram.messages)


def test_ten_message_cap_blocks_new_rubric_before_llm(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    path = tmp_path/'state.db'
    storage = Storage(path)
    for index in range(10):
        storage.save(FeedItem('Test', f'Ранее {index}', f'https://example.org/old-{index}'),
                     'PUBLISHED', telegram_message_id=50+index)
    storage.close()
    telegram = setup_pipeline(monkeypatch, [article(TRANSLATION).item])
    writer = RubricWriter()
    monkeypatch.setattr(main, 'choose_rewriter', lambda: writer)
    assert main.run(main.parse_args(['--state', str(path), '--author-feature', 'translation'])).sent == 0
    assert not writer.drafts and not telegram.messages


def test_reader_request_keeps_priority_over_a_new_satirical_rubric(monkeypatch, tmp_path):
    freeze_moscow_evening(monkeypatch, NOW)
    source = {'name': 'The Insider', 'homepage': 'https://theins.ru/', 'enabled': True}
    path = tmp_path/'state.db'
    storage = Storage(path)
    handle_reader_command(storage, '/suggest https://theins.ru/news/987', 456, [source], 'test-only', NOW)
    storage.close()
    item = article(TRANSLATION).item
    item.source, item.url = 'The Insider', 'https://theins.ru/news/987'
    telegram = setup_pipeline(monkeypatch, [item])
    monkeypatch.setattr(main, 'load_sources', lambda path: [source])
    writer = RubricWriter()
    monkeypatch.setattr(main, 'choose_rewriter', lambda: writer)
    main.run(main.parse_args(['--state', str(path), '--author-feature', 'auto']))
    public = [body for chat, body in telegram.messages if chat == '@channel']
    assert 'Разбор по запросу' in public[0] and not writer.drafts
