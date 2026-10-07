from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from rapidfuzz import fuzz

TRACKING_PARAMS = {"fbclid", "gclid", "yclid", "ref", "source"}


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    port = f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
    ]
    return urlunsplit(((parts.scheme or "https").lower(), host + port, path, urlencode(sorted(query)), ""))


def normalize_title(title: str) -> str:
    value = title.lower().replace("ё", "е")
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


class Deduplicator:
    def __init__(self, storage: object, fuzzy_threshold: int = 84):
        self.storage = storage
        self.fuzzy_threshold = fuzzy_threshold

    def find_duplicate(self, url: str, title: str) -> tuple[bool, str | None]:
        normalized_url = normalize_url(url)
        normalized_title = normalize_title(title)
        exact = self.storage.find_exact(normalized_url, normalized_title)
        if exact:
            return True, f"exact:{exact}"
        for row in self.storage.recent_titles(limit=500):
            old_title = row["normalized_title"]
            score = fuzz.token_set_ratio(normalized_title, old_title)
            if score >= self.fuzzy_threshold:
                return True, f"fuzzy:{score}:{row['url']}"
            if _same_named_event(normalized_title, old_title):
                return True, f"event:{score}:{row['url']}"
        return False, None


EVENT_GROUPS = (
    ("арест", "сизо", "задерж", "заключен", "страж"),
    ("приговор", "колони", "срок", "осуд"),
    ("атак", "обстрел", "удар", "беспилот", "дрон"),
    ("обыск", "уголовн", "дело", "возбуд"),
    ("санкц", "ограничен", "эмбарго"),
)
GENERIC_WORDS = {
    "который", "дело", "уголовное", "новый", "россии", "российский", "суд", "после",
    "против", "заявил", "сообщил", "человек", "власти",
}

UPDATE_PATTERNS = (
    "обнов", "новые данные", "стало известно", "число .*вырос", "число .*увелич",
    "уточнил", "уточнили", "подтвердил", "подтвердили", "дополнил", "дополнили",
)


def is_meaningful_update(title: str, description: str = "") -> bool:
    value = f"{title} {description}".lower()
    return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in UPDATE_PATTERNS)


def _same_named_event(left: str, right: str) -> bool:
    left_groups = {i for i, group in enumerate(EVENT_GROUPS) if any(word in left for word in group)}
    right_groups = {i for i, group in enumerate(EVENT_GROUPS) if any(word in right for word in group)}
    if not left_groups.intersection(right_groups):
        return False
    left_words = {word for word in left.split() if len(word) >= 6 and word not in GENERIC_WORDS}
    right_words = {word for word in right.split() if len(word) >= 6 and word not in GENERIC_WORDS}
    return bool(left_words.intersection(right_words))
