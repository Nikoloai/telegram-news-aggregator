from datetime import datetime, timezone

import pytest

import main
from aggregator.editorial import is_emergency, review_reasons
from aggregator.formatter import format_post
from aggregator.models import Article, FeedItem
from aggregator.modes import RewriteMode, editorial_mode, satirical_tail
from aggregator.storage import Storage
from rewriter.llm import LLMRewriter, SYSTEM_PROMPT
from tests.test_pipeline import setup_pipeline


@pytest.mark.parametrize('title,text', [
    ('Госдума России приняла новый закон о налогах', ''),
    ('Роскомнадзор объяснил блокировку сервиса', ''),
    ('Министра задержали по подозрению в коррупции', 'Следствие заявило о взятке.'),
    ('Российский дипломат, высланный после взрывов, работает в посольстве',
     'Дипломат получил аккредитацию на такую же должность.'),
    ('Кадыров получил почётную награду', 'Ранее он получал награды во время войны.'),
])
def test_suitable_news_is_satirical_by_default(monkeypatch, title, text):
    monkeypatch.delenv('EDITORIAL_STYLE', raising=False)
    assert editorial_mode(title, text) == RewriteMode.SATIRICAL


@pytest.mark.parametrize('title,text', [
    ('После открытия фестиваля погибли люди', 'Чиновник получил награду.'),
    ('Кадыров получил награду', 'В ходе мероприятия пострадали дети.'),
    ('Министр заявил об аресте журналиста', 'Министр получил новую должность.'),
    ('Суд отправил активиста в колонию', ''),
    ('В России задержали участника антивоенной акции', ''),
    ('После импортозамещения произошло отравление', ''),
    ('Министр сообщил о пожаре', 'Министр получил награду.'),
    ('Российская армия атаковала город', 'Ведомство заявило о наградах.'),
    ('В Москве после взрыва началась эвакуация', ''),
    ('В Москве началась эвакуация жителей', ''),
    ('В России арестовали человека', 'Министр получил награду.'),
])
def test_serious_topics_override_authorial_style(title, text):
    assert editorial_mode(title, text, 'satirical') == RewriteMode.HARD_NEWS


def test_legacy_news_style_is_available(monkeypatch):
    monkeypatch.setenv('EDITORIAL_STYLE', 'news')
    assert editorial_mode('Госдума приняла новый закон о налогах') == RewriteMode.ANALYSIS
    assert editorial_mode('Роскомнадзор объяснил очередную блокировку') == RewriteMode.IRONIC


def test_review_allows_public_career_but_holds_victim_satire():
    career = 'Российский дипломат, высланный после взрывов, работает в посольстве.\n\nАдрес новый, работа прежняя.'
    assert not review_reasons(career, career, RewriteMode.SATIRICAL, [], [])
    victims = 'Чиновник открыл фестиваль. На открытии погибли люди.'
    assert review_reasons(victims, victims, RewriteMode.SATIRICAL, [], [])


def test_old_explosion_background_does_not_force_a_career_post_to_emergency():
    now = datetime.now(timezone.utc)
    item = FeedItem('Test', 'Российский дипломат, высланный после взрывов, работает в посольстве',
                    'https://example.org/career', 'Дипломат получил аккредитацию на такую же должность.', now)
    assert not is_emergency(item, ['military'], now)
    item.description = 'Во время взрыва пострадали люди.'
    assert is_emergency(item, ['military'], now)


def test_prompt_requires_full_authorial_satire_and_no_fiction():
    assert 'не нейтральная новостная лента' in SYSTEM_PROMPT
    assert 'Сатирическая мысль должна проходить через весь текст' in SYSTEM_PROMPT
    assert 'Пиши как живой человек' in SYSTEM_PROMPT
    assert 'Не выдумывай указы, должности, награды, суммы' in SYSTEM_PROMPT
    assert 'HARD_NEWS всегда важнее' in SYSTEM_PROMPT


def test_llm_request_uses_selected_satirical_mode(monkeypatch):
    requests = []
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {'choices': [{'message': {'content': 'Тестовый текст'}}]}
    monkeypatch.setattr('rewriter.llm.requests.post', lambda *args, **kwargs: requests.append(kwargs) or Response())
    item = FeedItem('Test', 'Госдума России приняла закон', 'https://example.org')
    writer = LLMRewriter('openai', 'test-only', 'test-model')
    for mode in (RewriteMode.SATIRICAL, RewriteMode.HARD_NEWS):
        writer.rewrite(Article(item, item.title), mode)
    assert requests[0]['json']['temperature'] == 0.4
    assert 'Режим: SATIRICAL' in requests[0]['json']['messages'][1]['content']
    assert requests[1]['json']['temperature'] == 0.2


def test_only_short_numberless_ending_can_be_reused():
    ending = 'Платить предлагают живыми деньгами. Радоваться, видимо, придётся воображаемыми.'
    assert satirical_tail('Факты источника.\n\n' + ending) == ending
    for ending in ('На этом заработали 999 миллионов.', 'Чиновник заявил: «Теперь всё прекрасно».',
                   'Читайте https://example.org/news', 'x' * 321):
        assert satirical_tail('Факты.\n\n' + ending) is None
    assert satirical_tail('Один абзац без отдельной концовки.') is None


def test_formatter_keeps_commentary_separate_and_escaped():
    post = format_post('Расчёт редакции: +20 рублей.', 'Test', 'https://example.org',
                       feature_title='Цена решения', author_commentary='Очень <щедро>.', max_chars=2200)
    assert 'Расчёт редакции: +20 рублей.\n\nОчень &lt;щедро&gt;.' in post
    assert post.count('Очень') == 1
    assert 'Стабильность' not in format_post('Авторская мысль', 'Test', 'https://example.org', mode=RewriteMode.SATIRICAL)


class MockAuthor:
    def __init__(self):
        self.modes = []
    def rewrite(self, article, mode):
        self.modes.append(mode)
        return article.item.title + '\n\n' + article.text + '\n\nПлатить предлагают живыми деньгами. Радоваться, видимо, придётся воображаемыми.'


def test_pipeline_uses_satire_and_preserves_feature_calculation(monkeypatch, tmp_path):
    monkeypatch.setenv('EDITORIAL_STYLE', 'satirical')
    item = FeedItem('Test', 'В России увеличили тариф на услугу', 'https://example.org/tariff',
                    'В России тариф вырос с 100 до 120 рублей в месяц.', datetime.now(timezone.utc))
    telegram = setup_pipeline(monkeypatch, [item])
    writer = MockAuthor()
    monkeypatch.setattr(main, 'choose_rewriter', lambda: writer)
    path = tmp_path/'state.db'
    result = main.run(main.parse_args(['--state', str(path), '--author-feature', 'cost']))
    public = [text for chat, text in telegram.messages if chat == '@channel']
    assert result.sent == 1 and writer.modes == [RewriteMode.SATIRICAL]
    assert '240 руб.' in public[0] and 'воображаемыми' in public[0]
    storage = Storage(path)
    assert storage.connection.execute('SELECT rewrite_mode FROM articles').fetchone()['rewrite_mode'] == 'SATIRICAL'
    storage.close()


def test_pipeline_keeps_corrections_non_satirical(monkeypatch, tmp_path):
    monkeypatch.setenv('EDITORIAL_STYLE', 'satirical')
    item = FeedItem('Test', 'Уточнение: в России принят закон о налогах', 'https://example.org/correction',
                    'Госдума приняла закон о налогах.', datetime.now(timezone.utc))
    setup_pipeline(monkeypatch, [item])
    modes = []
    class Writer:
        def rewrite(self, article, mode):
            modes.append(mode)
            return article.item.title + '\n\n' + article.text
    monkeypatch.setattr(main, 'choose_rewriter', Writer)
    main.run(main.parse_args(['--state', str(tmp_path/'state.db')]))
    assert modes == [RewriteMode.ANALYSIS]


def test_ordinary_post_uses_whole_authorial_text(monkeypatch, tmp_path):
    monkeypatch.setenv('EDITORIAL_STYLE', 'satirical')
    item = FeedItem('Test', 'В России увеличили тариф на услугу', 'https://example.org/tariff',
                    'В России тариф вырос с 100 до 120 рублей в месяц.', datetime.now(timezone.utc))
    telegram = setup_pipeline(monkeypatch, [item])
    writer = MockAuthor()
    monkeypatch.setattr(main, 'choose_rewriter', lambda: writer)
    result = main.run(main.parse_args(['--state', str(tmp_path/'state.db')]))
    public = [text for chat, text in telegram.messages if chat == '@channel']
    assert result.sent == 1 and writer.modes == [RewriteMode.SATIRICAL]
    assert '100 до 120 рублей' in public[0] and public[0].count('воображаемыми') == 1


def test_provider_failure_never_adds_a_fabricated_punchline(monkeypatch, tmp_path):
    monkeypatch.setenv('EDITORIAL_STYLE', 'satirical')
    item = FeedItem('Test', 'В России увеличили тариф на услугу', 'https://example.org/tariff',
                    'В России тариф вырос с 100 до 120 рублей в месяц.', datetime.now(timezone.utc))
    telegram = setup_pipeline(monkeypatch, [item])
    class FailingAuthor:
        def rewrite(self, article, mode):
            raise RuntimeError('Test provider failure')
    monkeypatch.setattr(main, 'choose_rewriter', FailingAuthor)
    result = main.run(main.parse_args(['--state', str(tmp_path/'state.db'), '--author-feature', 'cost']))
    public = [text for chat, text in telegram.messages if chat == '@channel']
    assert result.sent == 1 and '240 руб.' in public[0] and 'воображаемыми' not in public[0]
