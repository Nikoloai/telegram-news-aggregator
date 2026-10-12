from datetime import datetime, timedelta, timezone

from aggregator.features import build_cost, valid_cost_body
from aggregator.models import Article, FeedItem
from aggregator.review import process_editor_messages
from aggregator.storage import Storage
from tests.test_review import FakeTelegram, private_command


def test_editor_recomputes_cost_and_rejects_tampered_arithmetic(tmp_path):
    storage = Storage(tmp_path/'state.db')
    item = FeedItem('Test', 'В России вырос тариф', 'https://example.org/cost')
    source = 'В России тариф вырос с 100 до 120 рублей в месяц.'
    draft = build_cost(Article(item, source))
    assert valid_cost_body(item, source, draft.body)
    bad = draft.body.replace('240 руб.', '999 руб.')
    assert not valid_cost_body(item, source, bad)
    bad_id = storage.enqueue_review(item, bad, source, 'ANALYSIS', ['state_policy'], ['Проверка'],
                                    rubric='cost', body_text=bad)
    good_id = storage.enqueue_review(item, draft.body, source, 'ANALYSIS', ['state_policy'], ['Проверка'],
                                     rubric='cost', body_text=draft.body)
    telegram = FakeTelegram([private_command(1, 123, f'/publish {bad_id}'),
                             private_command(2, 123, f'/publish {good_id}')])
    now = datetime.now(timezone.utc)
    sent = process_editor_messages(storage, telegram, '123', '@channel', now-timedelta(hours=1), now+timedelta(hours=1), 10, True)
    assert sent == 1
    assert storage.get_review(bad_id)['status'] == 'PENDING'
    assert storage.get_review(good_id)['status'] == 'PUBLISHED'
    storage.close()


def test_editor_cannot_publish_second_author_rubric_in_one_day(tmp_path):
    storage = Storage(tmp_path/'state.db')
    item = FeedItem('Test', 'Российские власти приняли закон', 'https://example.org/new')
    body = item.title
    row_id = storage.enqueue_review(item, body, body, 'ANALYSIS', ['state_policy'], ['Проверка'],
                                    rubric='reader', body_text=body)
    storage.save(FeedItem('Test', 'Ранее опубликовано', 'https://example.org/old'), 'PUBLISHED',
                 telegram_message_id=50, rubric='responsibility')
    telegram = FakeTelegram([private_command(1, 123, f'/publish {row_id}')])
    now = datetime.now(timezone.utc)
    assert process_editor_messages(storage, telegram, '123', '@channel', now-timedelta(hours=1), now+timedelta(hours=1), 10, True) == 0
    assert 'Авторская рубрика' in telegram.messages[0][1]
    storage.close()
