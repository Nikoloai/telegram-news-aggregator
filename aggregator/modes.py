from __future__ import annotations

import re
from enum import StrEnum


class RewriteMode(StrEnum):
    HARD_NEWS = "HARD_NEWS"
    ANALYSIS = "ANALYSIS"
    IRONIC = "IRONIC"


HARD_PATTERNS = (
    "погиб", "ранен", "убит", "войн", "обстрел", "атак", "взрыв", "катастроф", "пожар", "горит",
    "арест", "задерж", "уголовн.*дел", "сизо", "колони", "пытк", "насили", "политзаключ",
)
IRONIC_PATTERNS = (
    "запретил.*запрещ", "цензур", "пропаганд", "противореч", "абсурд", "курьез",
    "чиновник.*заявил", "роскомнадзор", "блокиров.*за",
)
ANALYSIS_PATTERNS = (
    "закон", "указ", "санкц", "эконом", "бюджет", "налог", "госдум", "правительств",
    "международ", "решени", "реформ", "политическ.*процесс",
)


def _has(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<![а-яёa-z0-9])(?:{pattern})", text, flags=re.IGNORECASE) for pattern in patterns)


def classify_mode(title: str, text: str = "") -> RewriteMode:
    value = f"{title} {text}".lower()
    # Safety always overrides tone opportunities.
    if _has(value, HARD_PATTERNS):
        return RewriteMode.HARD_NEWS
    if _has(value, IRONIC_PATTERNS):
        return RewriteMode.IRONIC
    if _has(value, ANALYSIS_PATTERNS):
        return RewriteMode.ANALYSIS
    return RewriteMode.ANALYSIS
