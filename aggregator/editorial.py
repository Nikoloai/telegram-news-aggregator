from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from .corroboration import stories_match
from .models import FeedItem
from .modes import RewriteMode, classify_mode


class ConfirmationLevel(StrEnum):
    MULTIPLE_SOURCES = "🟢 Подтверждено несколькими источниками"
    SINGLE_SOURCE = "🟡 Пока один источник"
    PARTY_CLAIM = "🔴 Заявление стороны"


CLAIM_PATTERNS = (
    r"\bзаяви(?:л|ла|ли|ло)\b",
    r"\bутвержда(?:ет|ют|л[аи]?)\b",
    r"\bпо словам\b",
    r"\bсообщи(?:л|ла|ли|ло)\b",
    r"\bпо данным (?:минобороны|властей|ведомства)\b",
)
EMERGENCY_PATTERNS = (
    r"\bсрочно\b",
    r"\bэкстренн",
    r"\bтеракт",
    r"\bмассированн.*атак",
    r"\bчрезвычайн.*ситуац",
    r"\bэвакуац",
    r"\bвзрыв",
    r"\bпогибли?\b",
)
CORRECTION_PATTERNS = (
    r"\bисправлен",
    r"\bисправили\b",
    r"\bопроверж",
    r"\bранее сообщалось ошибочно\b",
    r"\bне соответств(?:ует|овала?) действительности\b",
    r"\bуточнение\b",
)
PROMISE_PATTERNS = (
    r"\bобещал",
    r"\bпообещал",
    r"\bпланировал",
    r"\bдолжен был\b",
    r"\bзапустит",
    r"\bпостроит",
)
RESULT_PATTERNS = (
    r"\bне заработал",
    r"\bне запустил",
    r"\bне выполнил",
    r"\bсорван",
    r"\bпровал",
    r"\bперенес",
    r"\bотлож",
    r"\bсбой",
    r"\bподорож",
)
QUOTE_RE = re.compile(r"«([^»]{25,240})»")


def confirmation_level(text: str, corroborated_by: list[str]) -> ConfirmationLevel:
    if corroborated_by:
        return ConfirmationLevel.MULTIPLE_SOURCES
    lowered = text.lower()
    if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in CLAIM_PATTERNS):
        return ConfirmationLevel.PARTY_CLAIM
    return ConfirmationLevel.SINGLE_SOURCE


def is_emergency(item: FeedItem, topics: list[str], now_utc: datetime | None = None) -> bool:
    if item.published_at is None:
        return False
    now = now_utc or datetime.now(timezone.utc)
    published = item.published_at.astimezone(timezone.utc)
    if published < now - timedelta(hours=3) or published > now + timedelta(minutes=15):
        return False
    text = f"{item.title} {item.description}".lower()
    return bool(set(topics).intersection({"war", "military", "repression"})) and any(
        re.search(pattern, text, flags=re.IGNORECASE) for pattern in EMERGENCY_PATTERNS
    )


def is_correction(title: str, text: str = "") -> bool:
    value = f"{title} {text}".lower()
    return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in CORRECTION_PATTERNS)


def extract_title_quote(title: str) -> str | None:
    match = QUOTE_RE.search(title)
    return match.group(1).strip() if match else None


def related_publication(title: str, rows: Iterable[Mapping[str, object]]) -> Mapping[str, object] | None:
    for row in rows:
        previous_title = str(row["title"])
        if stories_match(title, previous_title, threshold=69):
            return row
    return None


def promise_result_publication(
    title: str,
    text: str,
    rows: Iterable[Mapping[str, object]],
) -> Mapping[str, object] | None:
    current = f"{title} {text}".lower()
    if not any(re.search(pattern, current, flags=re.IGNORECASE) for pattern in RESULT_PATTERNS):
        return None
    for row in rows:
        previous_content = row["content"] if "content" in row.keys() else ""
        previous = f"{row['title']} {previous_content or ''}".lower()
        if not any(re.search(pattern, previous, flags=re.IGNORECASE) for pattern in PROMISE_PATTERNS):
            continue
        if stories_match(title, str(row["title"]), threshold=62):
            return row
    return None


def rotate_items(items: list[FeedItem], slot: int) -> list[FeedItem]:
    preferences = (
        {RewriteMode.HARD_NEWS},
        {RewriteMode.ANALYSIS},
        {RewriteMode.IRONIC, RewriteMode.SATIRICAL},
        {RewriteMode.ANALYSIS},
    )
    preferred = preferences[slot % len(preferences)]
    recent, remainder = items[:30], items[30:]
    recent.sort(
        key=lambda item: classify_mode(item.title, item.description) not in preferred
    )
    return recent + remainder


def select_weekly_highlights(
    rows: Iterable[Mapping[str, object]],
    limit: int = 5,
) -> list[Mapping[str, object]]:
    """Select consequential, varied stories instead of merely the latest ones."""
    topic_weights = {
        "war": 5,
        "repression": 5,
        "corruption": 4,
        "state_policy": 3,
        "sanctions_economy": 3,
        "propaganda": 2,
        "military": 2,
    }
    ranked = sorted(
        rows,
        key=lambda row: (
            max(
                (
                    topic_weights.get(topic, 1)
                    for topic in str(row["topics"] or "").split(",")
                    if topic
                ),
                default=1,
            ),
            str(row["updated_at"] or ""),
        ),
        reverse=True,
    )
    selected: list[Mapping[str, object]] = []
    for row in ranked:
        if any(stories_match(str(row["title"]), str(existing["title"]), threshold=69) for existing in selected):
            continue
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected
