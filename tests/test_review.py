from datetime import datetime, timedelta, timezone

from aggregator.models import FeedItem
from aggregator.review import notify_pending, process_editor_messages
from aggregator.storage import Storage


class FakeTelegram:
    def __init__(self, updates=None):
        self.updates = updates or []
        self.messages = []

    def get_updates(self, offset=0):
        return [update for update in self.updates if update["update_id"] >= offset]

    def send(self, chat_id, text, preview_url=None):
        self.messages.append((chat_id, text))
        return len(self.messages)


def private_command(update_id, sender_id, text):
    return {"update_id": update_id, "message": {"chat": {"id": sender_id, "type": "private"},
            "from": {"id": sender_id}, "text": text}}


def queue_post(storage):
    item = FeedItem("Test", "Российские власти приняли закон", "https://example.org/review")
    body = "Российские власти приняли закон"
    review_id = storage.enqueue_review(item, body, body, "ANALYSIS", ["state_policy"], ["Спорные данные"], body_text=body)
    storage.save(item, "REVIEW_PENDING")
    return review_id


def test_only_configured_editor_can_publish_and_replay_does_not_duplicate(tmp_path):
    storage = Storage(tmp_path / "state.db")
    review_id = queue_post(storage)
    telegram = FakeTelegram([private_command(1, 999, f"/publish {review_id}"),
                             private_command(2, 123, f"/publish {review_id}")])
    now = datetime.now(timezone.utc)
    args = (storage, telegram, "123", "@channel", now-timedelta(hours=1), now+timedelta(hours=1), 10, True)
    assert process_editor_messages(*args) == 1
    assert process_editor_messages(*args) == 0
    assert len([entry for entry in telegram.messages if entry[0] == "@channel"]) == 1
    assert storage.get_review(review_id)["status"] == "PUBLISHED"
    storage.close()


def test_daily_limit_and_rejected_post_never_reach_channel(tmp_path):
    storage = Storage(tmp_path / "state.db")
    review_id = queue_post(storage)
    storage.save(FeedItem("Test", "Другой пост", "https://example.org/existing"), "PUBLISHED", telegram_message_id=10)
    telegram = FakeTelegram([private_command(1, 123, f"/publish {review_id}"),
                             private_command(2, 123, f"/skip {review_id}")])
    now = datetime.now(timezone.utc)
    process_editor_messages(storage, telegram, "123", "@channel", now-timedelta(hours=1), now+timedelta(hours=1), 1, True)
    assert not any(chat == "@channel" for chat, _ in telegram.messages)
    assert storage.get_review(review_id)["status"] == "REJECTED"
    storage.close()


def test_notification_retries_and_never_assumes_a_group_is_the_editor(tmp_path):
    storage = Storage(tmp_path / "state.db")
    queue_post(storage)
    telegram = FakeTelegram()
    notify_pending(storage, telegram, "-100123")
    assert not telegram.messages
    notify_pending(storage, telegram, "123")
    notify_pending(storage, telegram, "123")
    assert len(telegram.messages) == 1
    assert "/publish" in telegram.messages[0][1]
    storage.close()


def test_id_command_replies_in_same_private_chat_without_granting_editor_access(tmp_path):
    storage = Storage(tmp_path / "state.db")
    telegram = FakeTelegram([private_command(1, 123, "/id")])
    now = datetime.now(timezone.utc)
    process_editor_messages(storage, telegram, "", "@channel", now-timedelta(hours=1), now+timedelta(hours=1), 10, True)
    assert telegram.messages == [("123", "Твой ID для EDITOR_CHAT_ID: <code>123</code>")]
    assert storage.get_meta("editor_chat_id") is None
    storage.close()
