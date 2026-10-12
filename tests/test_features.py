from datetime import datetime, timedelta, timezone

from aggregator.features import (
    build_responsibility, build_cost, build_contrast, build_followup,
    followup_match, same_subject, select_feature,
)
from aggregator.models import Article, FeedItem
from aggregator.storage import Storage

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
ZONE = timezone(timedelta(hours=3))


def article(text, title='В России изменился тариф', url='https://example.org/news'):
    return Article(FeedItem('Test', title, url, text, NOW), text)


def test_responsibility_requires_roles_and_preserves_qualifiers():
    text = ('По версии следствия, подрядчик нарушил условия контракта.\n'
            'Заказчиком проекта названа администрация города.\n'
            'Стоимость контракта составила 120 млн рублей.\n'
            'Строительство больницы не завершено в срок.')
    draft = build_responsibility(article(text))
    assert draft.rubric == 'responsibility'
    assert all(evidence in text for evidence in draft.evidence)
    assert 'По версии следствия' in draft.body
    assert 'не доказывает вину' in draft.body
    assert build_responsibility(article('В России произошло событие, о котором сообщил журналист.')) is None


def test_cost_decimal_formula_and_conditional_annual_amount():
    draft = build_cost(article('В России тариф вырос с 100,50 до 125,75 рублей в месяц.'))
    assert '125,75 − 100,5 = 25,25 руб.' in draft.body
    assert '25,25 × 12 = 303 руб.' in draft.body
    assert 'условный расчёт' in draft.body
    assert 'прогноз тарифов' in draft.body


def test_cost_without_monthly_period_does_not_invent_yearly_cost():
    draft = build_cost(article('В России плата выросла с 100 до 120 рублей.'))
    assert '120 − 100 = 20 руб.' in draft.body
    assert 'за год' not in draft.body


def test_cost_handles_thousands_minutes_and_reduction():
    assert '2000 − 1000 = 1000 руб.' in build_cost(article('Плата выросла с 1 до 2 тыс. рублей.')).body
    assert '60 − 20 = 40 мин.' in build_cost(article('Время ожидания увеличилось с 20 до 60 минут.')).body
    assert '80 − 100 = -20 руб.' in build_cost(article('Плата снизилась с 100 до 80 рублей.')).body


def test_cost_rejects_ambiguous_or_missing_bases_and_proposals():
    for text in ['Тариф вырастет на 20 процентов.', 'Тариф может вырасти с 100 до 120 рублей.',
                 'Тариф вырос с 0 до 120 рублей.', 'Плата выросла с 100 рублей до 2 млн рублей.',
                 'Плата выросла с 100 долларов до 120 рублей.', 'Плата выросла с 100 до 100 рублей.']:
        assert build_cost(article(text)) is None


def history(claim, published=NOW):
    return [{'content': claim, 'published_at': published.isoformat(), 'source': 'Earlier',
             'url': 'https://example.org/claim', 'canonical_url': None}]


def test_contrast_requires_same_named_object_and_same_day():
    claim = 'В Самаре мэр заявил, что сервис «Сфера» полностью готов к запуску.'
    reality = 'В Самаре сервис «Сфера» не работает из-за сбоя.'
    draft = build_contrast(article(reality), history(claim), NOW, ZONE)
    assert draft.rubric == 'contrast'
    assert claim in draft.body and reality in draft.body
    assert 'не доказывает ложь' in draft.body
    assert draft.extra_sources == (('Earlier', 'https://example.org/claim'),)
    assert build_contrast(article(reality), history(claim, NOW - timedelta(days=1)), NOW, ZONE) is None


def test_contrast_rejects_random_same_city_events_and_different_periods():
    assert not same_subject('В Самаре чиновник сообщил об успехах медицины.',
                            'В Самаре чиновник рассказал об аварии в больнице.')
    current = article('В Самаре сервис «Сфера» не работает в октябре 2026 года.')
    old = 'В Самаре мэр заявил, что сервис «Сфера» полностью готов в сентябре 2026 года.'
    assert build_contrast(current, history(old), NOW, ZONE) is None
    assert build_contrast(current, history(old.replace('сентябре 2026', 'октябре 2025')), NOW, ZONE) is None


def test_followup_does_not_match_generic_russia_or_two_different_schools():
    assert not followup_match('Госдума России приняла закон об образовании',
                              FeedItem('Test', 'Госдума России изменила налог на автомобили', 'x'))
    assert not same_subject('В Самаре школа №7 завершила ремонт.', 'В Самаре школа №8 закрыта из-за аварии.')
    assert followup_match('Игорь Яковенко отправлен в СИЗО', FeedItem('Test', 'Игорь Яковенко обратился в суд', 'x'))


def test_no_news_followup_is_clearly_limited_to_observed_feeds():
    watch = {'title': 'В Самаре обещали открыть школу', 'source': 'Test', 'url': 'https://example.org/old',
             'started_at': (NOW-timedelta(days=9)).isoformat(), 'telegram_message_id': 50}
    draft = build_followup(watch, None, NOW, '@channel')
    assert 'Прошло 9 дней' in draft.body
    assert 'не доказывает, что проблема не решена' in draft.body
    assert ('Первый выпуск', 'https://t.me/channel/50') in draft.extra_sources


def test_watch_checkpoints_and_original_snapshot_survive_article_updates(tmp_path, monkeypatch):
    import aggregator.storage as module
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(module, 'datetime', Frozen)
    storage = Storage(tmp_path/'state.db')
    item = FeedItem('Test', 'В Самаре обещали открыть школу', 'https://example.org/old')
    storage.save(item, 'PUBLISHED', topics=['state_policy'], telegram_message_id=10)
    storage.watch_story(storage.recent_published()[0])
    assert not storage.due_watches(NOW+timedelta(days=6))
    watch = storage.due_watches(NOW+timedelta(days=7))[0]
    storage.finish_watch_checkpoint(watch['id'])
    assert not storage.due_watches(NOW+timedelta(days=29))
    item.title = 'В Самаре школу закрыли'
    storage.save(item, 'PUBLISHED', topics=['state_policy'], telegram_message_id=11)
    storage.watch_story(storage.recent_published()[0])
    watch = storage.due_watches(NOW+timedelta(days=30))[0]
    assert 'обещали' in watch['title']
    storage.finish_watch_checkpoint(watch['id'])
    assert not storage.due_watches(NOW+timedelta(days=31))
    storage.close()


def test_feature_rotation_never_forces_an_empty_rubric():
    assert select_feature(article('В России произошло обычное событие.'), [], NOW, ZONE) is None


def test_contrast_rejects_negated_success_and_relative_historical_dates():
    current = article('В Самаре сервис «Сфера» не работает из-за сбоя.')
    denied = 'В Самаре мэр заявил, что сервис «Сфера» не готов к запуску.'
    historical = 'В Самаре мэр заявил, что сервис «Сфера» полностью готов ещё в прошлом году.'
    assert build_contrast(current, history(denied), NOW, ZONE) is None
    assert build_contrast(current, history(historical), NOW, ZONE) is None


def test_followup_does_not_cross_city_just_because_project_name_matches():
    assert not followup_match('В Самаре сервис «Сфера» обещали открыть',
                              FeedItem('Test', 'В Омске сервис «Сфера» открыли', 'x'))


def test_huge_or_tiny_values_are_not_used_for_rounding_misleading_calculations():
    assert build_cost(article('Плата выросла с 0,001 до 0,002 рублей.')) is None
    assert build_cost(article('Плата выросла с 10000000000000000000000000000000000 до 20000000000000000000000000000000000 рублей.')) is None
