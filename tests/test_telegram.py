from __future__ import annotations

from typing import Any

from aggregator.telegram import TelegramClient


class FakeResponse:
    def __init__(self, result: dict[str, Any]):
        self._result = result
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"ok": True, "result": self._result}


def test_check_channel_access_for_admin(monkeypatch) -> None:
    replies = iter(
        [
            {"id": 123, "username": "telegram_news_aggregator_bot"},
            {"status": "administrator", "can_post_messages": True},
        ]
    )
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_get(url: str, params: dict[str, object], timeout: int) -> FakeResponse:
        calls.append((url, params))
        return FakeResponse(next(replies))

    monkeypatch.setattr("aggregator.telegram.requests.get", fake_get)
    access = TelegramClient("secret").check_channel_access("@channel")

    assert access.username == "telegram_news_aggregator_bot"
    assert access.status == "administrator"
    assert access.can_post_messages is True
    assert calls[0][0].endswith("/getMe")
    assert calls[1][0].endswith("/getChatMember")
    assert calls[1][1] == {"chat_id": "@channel", "user_id": 123}


def test_check_channel_access_rejects_member(monkeypatch) -> None:
    replies = iter(
        [
            {"id": 123, "username": "telegram_news_aggregator_bot"},
            {"status": "member"},
        ]
    )

    def fake_get(url: str, params: dict[str, object], timeout: int) -> FakeResponse:
        return FakeResponse(next(replies))

    monkeypatch.setattr("aggregator.telegram.requests.get", fake_get)
    access = TelegramClient("secret").check_channel_access("@channel")

    assert access.status == "member"
    assert access.can_post_messages is False


def test_discover_channels_from_membership_updates(monkeypatch) -> None:
    updates = [
        {
            "update_id": 1,
            "my_chat_member": {
                "chat": {
                    "id": -1001234567890,
                    "type": "channel",
                    "title": "Test channel",
                    "username": "test_channel",
                }
            },
        },
        {"update_id": 2, "message": {"text": "ignored"}},
    ]

    def fake_get(url: str, params: dict[str, object], timeout: int) -> FakeResponse:
        return FakeResponse(updates)  # type: ignore[arg-type]

    monkeypatch.setattr("aggregator.telegram.requests.get", fake_get)
    channels = TelegramClient("secret").discover_channels()

    assert len(channels) == 1
    assert channels[0].chat_id == -1001234567890
    assert channels[0].title == "Test channel"
    assert channels[0].username == "test_channel"


def test_send_uses_html_and_keeps_link_preview(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object], int]] = []

    def fake_post(url: str, json: dict[str, object], timeout: int) -> FakeResponse:
        calls.append((url, json, timeout))
        return FakeResponse({"message_id": 321})

    monkeypatch.setattr("aggregator.telegram.requests.post", fake_post)
    message_id = TelegramClient("secret").send("@channel", '<a href="https://example.org">Источник</a>')

    assert message_id == 321
    assert calls[0][0].endswith("/sendMessage")
    assert calls[0][1]["parse_mode"] == "HTML"
    assert calls[0][1]["link_preview_options"] == {"is_disabled": False}


def test_send_explicitly_previews_article_instead_of_earlier_post(monkeypatch) -> None:
    calls = []

    def fake_post(url, json, timeout):
        calls.append(json)
        return FakeResponse({"message_id": 1})

    monkeypatch.setattr("aggregator.telegram.requests.post", fake_post)
    TelegramClient("test").send("@channel", '<a href="https://t.me/channel/1">Ранее</a>', preview_url="https://example.org/article")
    assert calls[0]["link_preview_options"]["url"] == "https://example.org/article"


def test_send_photo_with_html_caption(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json))
        return FakeResponse({"message_id": 777})

    monkeypatch.setattr("aggregator.telegram.requests.post", fake_post)
    msg = TelegramClient("test").send(
        "@channel", '<b>Калининград</b> <a href="https://example.org/a">Источник</a>',
        image_url="https://example.org/news.jpg")
    assert msg == 777
    assert len(calls) == 1
    assert calls[0][0].endswith("/sendPhoto")
    assert calls[0][1]["photo"] == "https://example.org/news.jpg"
    assert calls[0][1]["parse_mode"] == "HTML"


def test_rejected_photo_falls_back_to_one_text_message(monkeypatch):
    calls = []

    class BadPhoto:
        status_code = 400

    def fake_post(url, json, timeout):
        calls.append(url)
        return BadPhoto() if url.endswith("/sendPhoto") else FakeResponse({"message_id": 778})

    monkeypatch.setattr("aggregator.telegram.requests.post", fake_post)
    assert TelegramClient("test").send("@channel", "Текст", image_url="https://example.org/bad.jpg") == 778
    assert [url.rsplit('/', 1)[-1] for url in calls] == ["sendPhoto", "sendMessage"]


def test_long_caption_falls_back_to_full_text_with_link_preview(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json))
        return FakeResponse({"message_id": 779})

    monkeypatch.setattr("aggregator.telegram.requests.post", fake_post)
    long_post = '<b>Новость</b>' + 'д' * 1030
    assert TelegramClient("test").send("@channel", long_post,
                                       preview_url="https://example.org/article",
                                       image_url="https://example.org/news.jpg") == 779
    assert len(calls) == 1 and calls[0][0].endswith("/sendMessage")
    assert calls[0][1]["text"] == long_post


def test_uncertain_photo_response_never_sends_second_post(monkeypatch):
    import requests
    calls = []

    def fake_post(url, json, timeout):
        calls.append(url)
        raise requests.Timeout("timeout after possible send")

    monkeypatch.setattr("aggregator.telegram.requests.post", fake_post)
    import pytest
    with pytest.raises(requests.Timeout):
        TelegramClient("test").send("@channel", "Пост", image_url="https://example.org/photo.jpg")
    assert len(calls) == 1 and calls[0].endswith("/sendPhoto")
