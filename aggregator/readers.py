"""Private, opt-in reader suggestions. No arbitrary URL fetching or auto-publication."""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from .dedupe import normalize_url
from .storage import Storage

READER_HELP = (
    "Можно предложить публичный материал для разбора:\n"
    "<code>/suggest https://адрес-материала</code>\n\n"
    "После ссылки можно указать: <code>цена</code>, <code>ответственность</code> или <code>сравнение</code>. "
    "Принимаем ссылки на подключённые новостные источники. "
    "Не присылай личные документы, контакты и данные других людей. "
    "Текст вопроса и Telegram ID в базу не переносим. Ссылку сохраняем в открытом виде "
    "только после её появления в публичной новостной ленте. "
    "Запрос не публикуется автоматически. Ответы обрабатываются при очередном запуске бота.\n"
    "До 3 разных ссылок за 24 часа. Удалить учёт своих запросов: /forget."
)


def reader_key(privacy_key: str, chat_id: int) -> str:
    if not privacy_key:
        raise ValueError("A private key is required for reader pseudonyms")
    return hmac.new(privacy_key.encode(), f"reader:{chat_id}".encode(), hashlib.sha256).hexdigest()


def request_key(privacy_key: str, url: str) -> str:
    if not privacy_key:
        return ''
    parts = urlsplit(url)
    public = urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))
    return hmac.new(privacy_key.encode(), f"reader-url:{normalize_url(public)}".encode(), hashlib.sha256).hexdigest()


def source_url(text: str, sources: list[dict[str, object]]) -> tuple[str, str] | None:
    links = re.findall(r'https?://[^\s<>"\[\]]+', text)
    if len(links) != 1:
        return None
    try:
        parts = urlsplit(links[0].rstrip(".,;!?)"))
        if parts.username or parts.password or parts.port not in (None, 443) or parts.scheme != "https":
            return None
        if not parts.hostname or len(parts.path) < 2 or len(parts.path) > 500:
            return None
        # Query strings and fragments may carry reader-supplied private text. Discard them.
        public = urlunsplit(("https", parts.hostname.lower(), parts.path, "", ""))
        for source in sources:
            if not source.get("enabled", True):
                continue
            host = urlsplit(str(source.get("homepage", ""))).hostname
            if host and parts.hostname.lower().removeprefix("www.") == host.lower().removeprefix("www."):
                return normalize_url(public), str(source["name"])
    except ValueError:
        return None
    return None


def request_angle(text: str) -> str:
    # Persist an allowlisted category, NOT the free-form private question.
    for word, angle in [('цена', 'cost'), ('ответственность', 'responsibility'), ('сравнение', 'contrast')]:
        if re.search(rf'(?:^|\s){word}(?:\s|$)', text, re.I):
            return angle
    return 'facts'


def handle_reader_command(storage: Storage, text: str, chat_id: int,
                          sources: list[dict[str, object]], privacy_key: str,
                          now: datetime | None = None) -> str | None:
    command = text.split(" ", 1)[0].split("@", 1)[0]
    if command in {"/help", "/start"}:
        return READER_HELP
    if command not in {"/suggest", "/request", "/forget"}:
        return None
    if not privacy_key:
        return "Приём запросов временно недоступен."
    key = reader_key(privacy_key, chat_id)
    if command == "/forget":
        storage.forget_reader(key)
        return "Учёт твоих запросов удалён. Уже опубликованные материалы канала это не изменяет."
    if len(text) > 1200:
        return "Пришли одну публичную ссылку без длинного текста и личных данных."
    source = source_url(text, sources)
    if not source:
        return ("Нужна одна HTTPS-ссылка на материал подключённого источника: "
                "Медиазона, ОВД-Инфо, Настоящее Время, Meduza или The Insider. "
                "Личные сообщения, документы и произвольные сайты не принимаем.")
    url, name = source
    request_id, status = storage.add_reader_request(key, request_key(privacy_key, url), name,
                                                   now or datetime.now(timezone.utc), request_angle(text))
    replies = {
        "limit": "Лимит: до 3 разных ссылок за 24 часа. Попробуй позже.",
        "full": "Очередь временно заполнена. Попробуй позже.",
        "duplicate": "Эта ссылка уже учтена от тебя. Повтор не увеличивает её приоритет.",
        "covered": "Материал по этой ссылке уже обработан редакцией.",
    }
    return replies.get(status, f"Запрос №{request_id} принят. Проверим материал, если он появится в доступной ленте. "
                       "Публикация и срок ответа не гарантированы. Личный текст вопроса не сохранён.")
