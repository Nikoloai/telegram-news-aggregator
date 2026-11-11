from datetime import datetime, timezone
from types import SimpleNamespace

import main
from aggregator.collector import FeedCollector, parse_telegram_page
from aggregator.corroboration import source_evidence
from aggregator.editorial import rank_news
from aggregator.filters import TopicFilter
from aggregator.models import FeedItem
from aggregator.readers import source_url
from aggregator.storage import Storage
from tests.test_pipeline import setup_pipeline

NOW = datetime.now(timezone.utc)
AUTHOR = {'name': 'Антон Гендриксон', 'type': 'telegram', 'feed_url': 'https://t.me/s/gndrksn',
          'homepage': 'https://t.me/gndrksn/', 'region': 'kaliningrad', 'requires_review': True}
PAGE = '''<div class="tgme_widget_message" data-post="gndrksn/123">
<div class="tgme_widget_message_text">В Калининграде обсуждают решение о земельном участке.<br>
Депутат прокомментировал изменение правил землепользования.
<a href="https://example.org/unrelated">Материал</a></div>
<a class="tgme_widget_message_date"><time datetime="2026-11-11T10:00:00Z"></time></a></div>'''


def test_public_channel_post_keeps_text_source_and_date():
    item, = parse_telegram_page(PAGE, AUTHOR)
    assert item.url == 'https://t.me/gndrksn/123'
    assert item.published_at == datetime(2026, 11, 11, 10, tzinfo=timezone.utc)
    assert 'землепользования' in item.description and item.source == AUTHOR['name']


def test_newsroom_channel_uses_its_own_article_link():
    source = dict(AUTHOR, homepage='https://www.newkaliningrad.ru/')
    page = PAGE.replace('https://example.org/unrelated', 'https://www.newkaliningrad.ru/news/123?utm_source=telegram')
    item, = parse_telegram_page(page, source)
    assert item.url == 'https://newkaliningrad.ru/news/123'


def test_reply_excerpt_cannot_replace_current_post_or_create_a_false_update():
    reply = '<a class="tgme_widget_message_reply"><div class="tgme_widget_message_text js-message_reply_text">Старое сообщение о стройке в Калининграде и прежних сроках.</div></a>'
    page = PAGE.replace('<div class="tgme_widget_message_text">', reply + '<div class="tgme_widget_message_text">', 1)
    item, = parse_telegram_page(page, AUTHOR)
    assert 'Старое сообщение' not in item.description and item.title.startswith('В Калининграде')
    only_reply = page.replace('class="tgme_widget_message_text">В Калининграде', 'class="unsupported">В Калининграде')
    assert not parse_telegram_page(only_reply, AUTHOR)


def test_newsroom_article_can_be_linked_in_a_separate_preview_card():
    source = dict(AUTHOR, homepage='https://rugrad.online/')
    page = PAGE.replace('</a></div>', '</a><a class="tgme_widget_message_link_preview" href="https://rugrad.online/news/123/"></a></div>')
    item, = parse_telegram_page(page, source)
    assert item.url == 'https://rugrad.online/news/123'


def test_media_only_undated_and_foreign_channel_embeds_are_skipped():
    assert not parse_telegram_page(PAGE.replace('gndrksn/123', 'anotherchannel/123'), AUTHOR)
    assert not parse_telegram_page(PAGE.replace('datetime="2026-11-11T10:00:00Z"', ''), AUTHOR)
    assert not parse_telegram_page(PAGE.replace('tgme_widget_message_text', 'image'), AUTHOR)


def test_collector_adds_region_and_editorial_review_metadata(monkeypatch):
    collector = FeedCollector()
    response = SimpleNamespace(content=PAGE.encode(), raise_for_status=lambda: None)
    monkeypatch.setattr(collector.session, 'get', lambda *args, **kwargs: response)
    item, = collector.collect(AUTHOR)
    assert 'Калининградская область' in item.categories and 'editorial_review' in item.categories


def test_national_reprint_does_not_receive_regional_priority(monkeypatch):
    collector = FeedCollector()
    page = PAGE.replace('В Калининграде', 'В Москве')
    response = SimpleNamespace(content=page.encode(), raise_for_status=lambda: None)
    monkeypatch.setattr(collector.session, 'get', lambda *args, **kwargs: response)
    item, = collector.collect(AUTHOR)
    assert 'Калининградская область' not in item.categories
    assert 'editorial_review' in item.categories


def test_telegram_suggestions_are_scoped_to_configured_channel():
    assert source_url('https://t.me/gndrksn/123', [AUTHOR])
    assert source_url('https://t.me/s/gndrksn/123', [AUTHOR])[0] == 'https://t.me/gndrksn/123'
    assert source_url('https://t.me/anotherchannel/123', [AUTHOR]) is None
    assert source_url('https://t.me/gndrksn', [AUTHOR]) is None


def test_local_public_interest_news_is_accepted_without_word_russia():
    item = FeedItem('RUGRAD', 'В Зеленоградске изменили правила землепользования', 'https://example.org/a',
                    categories=['Калининградская область'])
    result = TopicFilter().match(item)
    assert result.accepted and 'kaliningrad' in result.topics and 'state_policy' in result.topics
    item.title = 'Футбольный клуб выиграл матч'
    assert not TopicFilter().match(item).accepted


def test_named_regional_official_is_recognized_and_historical_namesake_is_not():
    result = TopicFilter().match(FeedItem('Test', 'Депутат Евгений Верхолаз представил проект', 'https://example.org/a'))
    assert result.accepted and 'kaliningrad' in result.topics
    other = TopicFilter().match(FeedItem('Test', 'Пётр Кропоткин и история анархизма', 'https://example.org/b'))
    assert not other.accepted


def test_regional_priority_does_not_displace_fresh_emergency():
    local = FeedItem('RUGRAD', 'В Калининграде изменили налог', 'https://example.org/local', published_at=NOW)
    ordinary = FeedItem('Test', 'В России изменили налог', 'https://example.org/ordinary', published_at=NOW)
    emergency = FeedItem('Test', 'В Москве после взрыва началась эвакуация', 'https://example.org/urgent', published_at=NOW)
    assert rank_news([ordinary, local], [], 0, NOW)[0] is local
    assert rank_news([ordinary, local, emergency], [], 0, NOW)[0] is emergency


def test_reprints_of_gendrikson_do_not_become_independent_confirmation():
    title = 'В Калининграде обсуждают изменение правил застройки'
    author = FeedItem(AUTHOR['name'], title, 'https://t.me/gndrksn/123', published_at=NOW)
    report = FeedItem('Новый Калининград', title, 'https://example.org/report',
                      'По словам Гендриксона, жители обсуждают изменение правил застройки.', NOW)
    assert 'Антон Гендриксон' in source_evidence(author, [author, report]).shared_origins


def test_author_source_is_held_even_if_general_uncertainty_review_is_disabled(monkeypatch, tmp_path):
    item = FeedItem(AUTHOR['name'], 'Депутат в Калининграде прокомментировал земельный спор',
                    'https://t.me/gndrksn/123', 'В авторском канале опубликовано мнение о решении по земельному участку.',
                    NOW, categories=['Калининградская область', 'editorial_review'])
    telegram = setup_pipeline(monkeypatch, [item])
    monkeypatch.setenv('REVIEW_UNCERTAIN', 'false')
    path = tmp_path/'state.db'
    assert main.run(main.parse_args(['--state', str(path), '--max-items', '1'])).sent == 0
    assert not any(channel == '@channel' for channel, _ in telegram.messages)
    storage = Storage(path)
    row = storage.connection.execute('SELECT reasons FROM review_queue').fetchone()
    assert row and 'Авторский источник' in row['reasons']
    storage.close()
