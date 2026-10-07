from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from .article import clean_html
from .corroboration import stories_match
from .models import FeedItem
from .modes import RewriteMode, classify_mode


class ConfirmationLevel(StrEnum):
    MULTIPLE_SOURCES = "🟢 О событии сообщают несколько изданий"
    SINGLE_SOURCE = "🟡 Пока один источник"
    PARTY_CLAIM = "🔴 Заявление стороны"
    SHARED_ORIGIN = "🟡 Несколько изданий ссылаются на один первоисточник"
    CONFLICTING = "🟠 В сообщениях есть расхождения"


CLAIM_PATTERNS = (
    r"\bзаяви(?:л|ла|ли|ло)\b",
    r"\bутвержда(?:ет|ют|л[аи]?)\b",
    r"\bпо словам\b",
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
    r"\bзапустил", r"\bзаработал", r"\bоткрыл", r"\bпостроил", r"\bвведен",
)
QUOTE_RE = re.compile(r"«([^»]{25,240})»")


def confirmation_level(
    text: str,
    corroborated_by: list[str],
    shared_origins: list[str] | None = None,
    conflicts: list[str] | None = None,
) -> ConfirmationLevel:
    if conflicts:
        return ConfirmationLevel.CONFLICTING
    lowered = text.lower()
    if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in CLAIM_PATTERNS):
        return ConfirmationLevel.PARTY_CLAIM
    if shared_origins:
        return ConfirmationLevel.SHARED_ORIGIN
    if corroborated_by:
        return ConfirmationLevel.MULTIPLE_SOURCES
    return ConfirmationLevel.SINGLE_SOURCE


def is_emergency(item: FeedItem, topics: list[str], now_utc: datetime | None = None) -> bool:
    if item.published_at is None:
        return False
    now = now_utc or datetime.now(timezone.utc)
    published = item.published_at.astimezone(timezone.utc)
    if published < now - timedelta(hours=3) or published > now + timedelta(minutes=15):
        return False
    lead = re.split(r"(?<=[.!?])\s+", clean_html(item.description), maxsplit=1)[0]
    text = f"{item.title} {lead}".lower()
    public_emergency = bool(set(topics).intersection({"war", "military", "repression", "human_rights"}))
    public_emergency = public_emergency or bool(re.search(r"\b(?:эвакуац|чрезвычайн|взрыв|пожар)", text))
    return public_emergency and any(
        re.search(pattern, text, flags=re.IGNORECASE) for pattern in EMERGENCY_PATTERNS
    )


def is_correction(title: str, text: str = "") -> bool:
    value = f"{title} {text}".lower()
    return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in CORRECTION_PATTERNS)


def extract_title_quote(title: str) -> str | None:
    # A quoted product or award name is not a statement by a speaker.
    attributed = re.search(
        r"(?:[:—]\s*|(?:заяв\w*|сказ\w*|утвержд\w*)\s+)«([^»]{25,240})»", title, re.I
    )
    return attributed.group(1).strip() if attributed else None


def publication_context(row: Mapping[str, object] | None, promise: bool = False) -> str | None:
    if row is None:
        return None
    text = clean_html(str(row["title"]))
    if promise and not any(re.search(pattern, text.lower()) for pattern in PROMISE_PATTERNS):
        content = clean_html(str(row["content"] or "")) if "content" in row.keys() else ""
        sentence = next(
            (part for part in re.split(r"(?<=[.!?])\s+", content)
             if any(re.search(pattern, part.lower()) for pattern in PROMISE_PATTERNS)),
            None,
        )
        if not sentence:
            return None
        text = sentence
    if len(text) > 320:
        return None  # Do not clip away a deadline or attribution from the old promise.
    date = ""
    if "updated_at" in row.keys() and row["updated_at"]:
        try:
            date = datetime.fromisoformat(str(row["updated_at"])).strftime("%d.%m.%Y")
        except ValueError:
            pass
    label = "Обещали" if promise else "Что было раньше"
    return f"{label}{' (выпуск от ' + date + ')' if date else ''}: «{text.rstrip('.')}»."


def review_reasons(
    source_text: str,
    body: str,
    mode: RewriteMode,
    conflicts: list[str],
    warnings: list[str],
) -> list[str]:
    reasons = list(conflicts) + list(warnings)
    if re.search(r"\b(?:неподтвержден\w*|слух\w*|достоверность.*не|не удалось подтвердить)\b", source_text, re.I):
        reasons.append("Источник прямо отмечает неподтвержденные сведения")
    if mode in {RewriteMode.IRONIC, RewriteMode.SATIRICAL}:
        if re.search(r"\b(?:идиот\w*|дебил\w*|твар\w*|мраз\w*|ублюд\w*)\b", body, re.I):
            reasons.append("Оскорбительная формулировка требует редакторской проверки")
        if classify_mode("", body) == RewriteMode.HARD_NEWS:
            reasons.append("Ироничный текст содержит тему жертв или репрессий")
    return list(dict.fromkeys(reasons))


def rank_news(
    items: list[FeedItem],
    recent_rows: Iterable[Mapping[str, object]],
    slot: int,
    now_utc: datetime | None = None,
    max_age_hours: int = 48,
) -> list[FeedItem]:
    now = now_utc or datetime.now(timezone.utc)
    recent = list(recent_rows)[:6]
    preferences = ({RewriteMode.HARD_NEWS}, {RewriteMode.ANALYSIS},
                   {RewriteMode.IRONIC, RewriteMode.SATIRICAL}, {RewriteMode.ANALYSIS})
    preferred = preferences[slot % 4]
    weights = (
        (r"погиб|ранен|взрыв|эвакуац|обстрел", 12),
        (r"арест|приговор|политзаключ|пытк", 9),
        (r"закон|налог|инфляц|санкц|блокиров|коррупц|хищен", 7),
        (r"награ|знак|пропаганд|импортозамещ", 3),
    )

    def score(item: FeedItem) -> float:
        primary = clean_html(item.title).lower()
        importance = max((weight for pattern, weight in weights if re.search(pattern, primary)), default=2)
        age = max(0, (now - item.published_at).total_seconds() / 3600) if item.published_at else 48
        repetition = sum(stories_match(item.title, str(row["title"])) for row in recent)
        varied = 1 if classify_mode(item.title, clean_html(item.description)) in preferred else 0
        return importance + max(0, 4 - age / 6) + varied - repetition * 8

    fresh = [item for item in items if not item.published_at or
             now - timedelta(hours=max_age_hours) <= item.published_at <= now + timedelta(minutes=15)]
    return sorted(fresh, key=score, reverse=True)


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
