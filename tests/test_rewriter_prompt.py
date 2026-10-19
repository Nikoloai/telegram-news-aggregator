from rewriter.llm import SYSTEM_PROMPT, HARD_SATIRE_PROMPT, AUTHOR_VOICE_PROMPT, LLMRewriter
from aggregator.models import Article, FeedItem
from aggregator.modes import RewriteMode
from aggregator.features import FeatureDraft


def test_prompt_requires_self_contained_specific_details() -> None:
    assert "понятен без перехода по ссылке" in SYSTEM_PROMPT
    assert "полное название сервиса" in SYSTEM_PROMPT
    assert "кто что сделал, когда и что именно произошло" in SYSTEM_PROMPT
    assert "что было указано неверно" in SYSTEM_PROMPT
    assert "сохраняй дословно" in SYSTEM_PROMPT
    assert "противопоставляй только факты" in SYSTEM_PROMPT


def test_prompt_requires_critical_but_proportionate_public_interest_angle():
    assert "критической редакционной позицией" in SYSTEM_PROMPT
    assert "что произошло → где система дала сбой → чем это обернулось для людей" in SYSTEM_PROMPT
    assert "причина пока неизвестна" in SYSTEM_PROMPT
    assert "Локальную проблему не превращай в общенациональную угрозу" in SYSTEM_PROMPT
    assert "без выдуманных медицинских прогнозов" in SYSTEM_PROMPT


def test_hard_satire_profile_requires_pressure_black_humor_and_final_sting():
    for requirement in ('максимально едкая', 'Сатирический напор', 'короткими фразами',
                        'чёрный юмор в адрес власти', 'Беспощадная концовка',
                        'не объясняй шутку после удара', 'не выдуманным ответом чиновника'):
        assert requirement in HARD_SATIRE_PROMPT


def test_hard_satire_does_not_loosen_factual_or_victim_boundaries():
    for requirement in ('не человеческое страдание', 'Не используй смерти, болезни, пытки',
                        'не желай людям вреда', 'HARD_NEWS важнее',
                        'сохраняй подозрение как подозрение', 'Не выдумывай виновность',
                        'не делай мат обязательной', 'не меняй только фамилию'):
        assert requirement in HARD_SATIRE_PROMPT


def test_strengthened_profile_is_sent_only_for_satire_and_keeps_same_writer(monkeypatch):
    requests = []
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {'choices': [{'message': {'content': 'Ответ тестового автора'}}]}
    monkeypatch.setattr('rewriter.llm.requests.post', lambda *args, **kwargs: requests.append(kwargs) or Response())
    writer = LLMRewriter('openai', 'test-only', 'test-model')
    article = Article(FeedItem('Test', 'В России увеличили тариф', 'https://example.org'),
                      'В России увеличили тариф на услугу.')
    for mode in RewriteMode:
        writer.rewrite(article, mode)
    writer.rewrite_commentary(article, FeatureDraft('career', 'Фактологическая основа.'))
    assert len(requests) == 5  # One existing author request per text; no additional editor.
    for mode, request in zip(RewriteMode, requests[:4]):
        system = request['json']['messages'][0]['content']
        assert AUTHOR_VOICE_PROMPT in system
        assert (HARD_SATIRE_PROMPT in system) == (mode == RewriteMode.SATIRICAL)
        assert request['json']['temperature'] == (0.4 if mode == RewriteMode.SATIRICAL else 0.2)
    last = requests[-1]['json']['messages']
    assert HARD_SATIRE_PROMPT in last[0]['content']
    assert 'один абзац на 60–320' in last[0]['content']
    assert 'беспощадную концовку' in last[1]['content']


def test_author_voice_avoids_formula_without_fabricating_human_experience():
    for requirement in ('одну мысль', 'Не объясняй читателю', 'риторическими вопросами',
                        'не выдумывай личный опыт', 'Не изображай очевидца',
                        'Не добавляй ошибки', 'атрибуция', 'Для HARD_NEWS'):
        assert requirement in AUTHOR_VOICE_PROMPT
    assert '2–4 абзаца по смыслу' in SYSTEM_PROMPT
