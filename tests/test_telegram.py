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
