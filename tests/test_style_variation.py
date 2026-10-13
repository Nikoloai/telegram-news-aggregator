from datetime import datetime, timezone

import pytest

import main
from aggregator.formatter import format_post
from aggregator.models import Article, FeedItem
from aggregator.modes import RewriteMode
from aggregator.storage import Storage
from rewriter.variation import repeated_phrases, editorial_text, varied_rewrite
from tests.test_pipeline import setup_pipeline


ENDING = 'Платить предлагают живыми деньгами. Радоваться, видимо, придётся воображаемыми.'
FRESH = 'Как удобно: решение чужое, а кошелёк почему-то ваш. Распределение ответственности получилось образцовое.'


def article():
    item = FeedItem('Test', 'В России увеличили тариф на услугу', 'https://example.org/new',
                    'В России тариф вырос с 100 до 120 рублей в месяц.', datetime.now(timezone.utc))
    return Article(item, item.description)


def test_detects_same_punchline_metaphor_and_near_duplicate():
    assert repeated_phrases(ENDING, [ENDING])
    assert repeated_phrases('Опять кадровая прачечная, знакомая работа.', ['Это кадровая прачечная.'])
    assert repeated_phrases('Система снова победила, а счета оплачивать вам.', ['Система снова победила и торжествует.'])
    assert repeated_phrases(ENDING.replace('видимо', 'пожалуй'), [ENDING])
    assert not repeated_phrases(FRESH, [ENDING])


def test_factual_source_quotes_and_numbers_are_not_classified_as_stale_jokes():
    source = article().text
    assert not repeated_phrases(source, [source], source)
    quote = 'Чиновник сказал, что аналогов нет.'
    assert not repeated_phrases(quote, [quote], quote)


def test_style_extraction_keeps_commentary_not_source_or_confirmation():
    post = format_post('Новость из источника.', 'Test', 'https://example.org',
                       feature_title='Перевод с чиновничьего', author_commentary=ENDING,
                       author_commentary_label='Авторский перевод (сатира)', confirmation='🟡 Пока один источник')
    text = editorial_text(post)
    assert ENDING in text
    assert 'Пока один источник' not in text and 'Источник:' not in text
    assert 'Перевод с чиновничьего' not in text and 'Авторский перевод' not in text


class Writer:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0
        self.memories = []
    def set_style_memory(self, memory):
        self.memories.append(memory)
    def rewrite(self, article, mode):
        value = self.outputs[min(self.calls, len(self.outputs)-1)]
        self.calls += 1
        return value


def test_one_retry_changes_the_repeated_construction():
    writer = Writer([ENDING, FRESH])
    assert varied_rewrite(writer, article(), RewriteMode.SATIRICAL, [ENDING]) == FRESH
    assert writer.calls == 2 and len(writer.memories) == 2
    assert any('Неприемлемый повтор' in value for value in writer.memories[-1])


def test_second_repeat_stops_after_exactly_two_calls():
    writer = Writer([ENDING])
    with pytest.raises(ValueError, match='Repeated'):
        varied_rewrite(writer, article(), RewriteMode.SATIRICAL, [ENDING])
    assert writer.calls == 2


def test_hard_news_never_gets_a_style_retry():
    writer = Writer([ENDING])
    varied_rewrite(writer, article(), RewriteMode.HARD_NEWS, [ENDING])
    assert writer.calls == 1 and writer.memories == [[]]


def test_public_style_memory_is_bounded_and_survives_reopening(tmp_path):
    path = tmp_path/'state.db'
    storage = Storage(path)
    for index in range(35):
        item = FeedItem('Test', f'Новость {index}', f'https://example.org/{index}')
        post = format_post(f'Новость {index}.\n\n{ENDING} Номер истории {index}.', 'Test', item.url)
        storage.save(item, 'PUBLISHED', rewrite_mode='SATIRICAL', telegram_message_id=index+1, post_html=post)
    assert len(storage.recent_style()) == 30
    assert 'Номер истории 34' in storage.recent_style()[0]
    assert all('Номер истории 0.' not in value for value in storage.recent_style())
    storage.close()
    storage = Storage(path)
    assert len(storage.recent_style()) == 30
    assert 'Номер истории 34' in storage.recent_style()[0]
    storage.close()


@pytest.mark.parametrize('status', ['DRY_RUN', 'REVIEW_READY', 'REVIEW_PENDING', 'SENT_TO_REVIEW', 'SEND_FAILED', 'REJECTED'])
def test_unpublished_posts_do_not_pollute_public_style_memory(tmp_path, status):
    storage = Storage(tmp_path/'state.db')
    storage.save(article().item, status, rewrite_mode='SATIRICAL', post_html=ENDING)
    assert not storage.recent_style()
    storage.close()


def seed_style(path):
    storage = Storage(path)
    old = FeedItem('Test', 'Прежняя новость', 'https://example.org/old')
    storage.save(old, 'PUBLISHED', rewrite_mode='SATIRICAL', telegram_message_id=50,
                 post_html=format_post('Другие факты.\n\n' + ENDING, 'Test', old.url))
    storage.close()


def test_pipeline_retries_repeated_joke_and_publishes_fresh_one(monkeypatch, tmp_path):
    path = tmp_path/'state.db'
    seed_style(path)
    telegram = setup_pipeline(monkeypatch, [article().item])
    monkeypatch.setenv('EDITORIAL_STYLE', 'satirical')
    writer = Writer([article().text + '\n\n' + ENDING, article().text + '\n\n' + FRESH])
    monkeypatch.setattr(main, 'choose_rewriter', lambda: writer)
    assert main.run(main.parse_args(['--state', str(path)])).sent == 1
    public = [body for chat, body in telegram.messages if chat == '@channel']
    assert writer.calls == 2 and FRESH in public[0] and ENDING not in public[0]


def test_pipeline_degrades_to_facts_when_the_joke_repeats_twice(monkeypatch, tmp_path):
    path = tmp_path/'state.db'
    seed_style(path)
    telegram = setup_pipeline(monkeypatch, [article().item])
    monkeypatch.setenv('EDITORIAL_STYLE', 'satirical')
    writer = Writer([article().text + '\n\n' + ENDING])
    monkeypatch.setattr(main, 'choose_rewriter', lambda: writer)
    assert main.run(main.parse_args(['--state', str(path)])).sent == 1
    public = [body for chat, body in telegram.messages if chat == '@channel']
    assert writer.calls == 2 and ENDING not in public[0] and '100 до 120 рублей' in public[0]


def test_existing_public_posts_backfill_style_memory_only_once(tmp_path):
    path = tmp_path/'state.db'
    storage = Storage(path)
    old = FeedItem('Test', 'Прежняя новость', 'https://example.org/old')
    storage.save(old, 'PUBLISHED', rewrite_mode='SATIRICAL', telegram_message_id=50,
                 post_html=format_post('Другие факты.\n\n' + ENDING, 'Test', old.url))
    storage.connection.execute('DELETE FROM style_memory')
    storage.connection.execute("DELETE FROM metadata WHERE key='style_memory_backfill'")
    storage.connection.commit()
    storage.close()
    for _ in range(2):
        storage = Storage(path)
        assert len(storage.recent_style()) == 1 and ENDING in storage.recent_style()[0]
        storage.close()


def test_second_post_in_same_release_already_sees_first_post_memory(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    items = [FeedItem('Test', 'В России чиновник заявил о цифровой реформе', 'https://example.org/first',
                      'В России чиновник представил цифровой сервис.', now),
             FeedItem('Test', 'В Москве увеличили плату за парковку', 'https://example.org/second',
                      'В Москве плата выросла с 50 до 80 рублей.', now)]
    telegram = setup_pipeline(monkeypatch, items)
    class SameRunWriter:
        calls = 0
        def rewrite(self, article, mode):
            self.calls += 1
            return article.text + '\n\n' + (ENDING if self.calls < 3 else FRESH)
    writer = SameRunWriter()
    monkeypatch.setattr(main, 'choose_rewriter', lambda: writer)
    main.run(main.parse_args(['--state', str(tmp_path/'state.db'), '--max-items', '2']))
    public = [body for chat, body in telegram.messages if chat == '@channel']
    assert len(public) == 2 and writer.calls == 3
    assert ENDING in public[0] and ENDING not in public[1] and FRESH in public[1]
