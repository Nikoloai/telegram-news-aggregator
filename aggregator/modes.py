from __future__ import annotations

import re
from enum import StrEnum


class RewriteMode(StrEnum):
    HARD_NEWS = "HARD_NEWS"
    ANALYSIS = "ANALYSIS"
    IRONIC = "IRONIC"
    SATIRICAL = "SATIRICAL"


HARD_PATTERNS = (
    "погиб", "ранен", "убит", "войн", "обстрел", "атак", "взрыв", "катастроф", "пожар", "горит",
    "эпидем", "отравлен", "заражен", "санитарн.*(?:угроз|опасност)",
    "арест", "задерж", "уголовн.*дел", "сизо", "колони", "пытк", "насили", "политзаключ",
)
IRONIC_PATTERNS = (
    "цензур", "противореч", "чиновник.*заявил", "роскомнадзор", "блокиров.*за",
)
SATIRICAL_PATTERNS = (
    "абсурд", "курьез", "пропаганд", "импортозамещ", "аналогов нет", "духовн",
    "традиционн.*ценност", "торжествен.*откр", "праздничн", "запретил.*запрещ",
    "патриотическ.*воспитан", "иноагент.*маркиров", "скреп", "гойда",
    "почетн.*архитектор", "чиновник.*наград", "кадыров.*(?:наград|знак)",
)
ANALYSIS_PATTERNS = (
    "закон", "указ", "санкц", "эконом", "бюджет", "налог", "госдум", "правительств",
    "международ", "решени", "реформ", "политическ.*процесс",
)


def _has(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<![а-яёa-z0-9])(?:{pattern})", text, flags=re.IGNORECASE) for pattern in patterns)


def classify_mode(title: str, text: str = "") -> RewriteMode:
    # The headline and lead define the event; background does not define the tone.
    lead = re.split(r"\n\s*\n|\n", text.strip(), maxsplit=1)[0]
    lead = " ".join(re.split(r"(?<=[.!?])\s+", lead)[:2])[:700]
    focal = f"{title} {lead}".lower()
    if _has(focal, HARD_PATTERNS):
        return RewriteMode.HARD_NEWS
    # A fresh report of victims anywhere in the material still overrides satire.
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
        historical = re.search(r"\b(?:ранее|прежде|напомним|в прошлом|до этого|в \d{4} году)\b", sentence, re.I)
        if not historical and _has(sentence, ("погиб", "ранен", "убит", "пытк", "пленн")):
            return RewriteMode.HARD_NEWS
    if _has(focal, SATIRICAL_PATTERNS):
        return RewriteMode.SATIRICAL
    if _has(focal, IRONIC_PATTERNS):
        return RewriteMode.IRONIC
    if _has(focal, ANALYSIS_PATTERNS):
        return RewriteMode.ANALYSIS
    return RewriteMode.ANALYSIS
