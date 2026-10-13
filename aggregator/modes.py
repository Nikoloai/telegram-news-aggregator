from __future__ import annotations

import os
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


VICTIM_PATTERNS = (
    'погиб', 'ранен', 'убит', 'жертв', 'пострад', 'гибел', 'смерт', 'сконч',
    r'умер(?:ла|ли|ло|еть)?\b', 'пытк', 'пленн', 'заложник', 'избиен', 'избили',
    'эпидем', 'отравлен', 'заражен', 'санитарн.*(?:угроз|опасност)', 'политзаключ',
)
PUBLIC_TARGETS = (
    'чиновник', 'министр', 'губернатор', 'мэр', 'депутат', 'кремл', 'путин', 'кадыров',
    'дипломат', 'военн.*атташе', 'мид', 'госдум', 'правительств', 'ведомств',
)
BUREAUCRATIC_TARGET = (
    'наград', 'почетн', 'знак',
    'должност', 'работ.*посольств', 'аккредитац', 'взятк', 'хищен', 'коррупц',
)
REPRESSION_VICTIMS = (
    'журналист', 'правозащит', 'оппозиционер', 'активист', 'протестующ',
    'митингующ', 'несовершеннолет', 'лгбт', 'антивоенн', 'пацифист',
)


def requires_serious_tone(title: str, text: str = '') -> bool:
    value = f'{title} {text}'.lower().replace('ё', 'е')
    if _has(value, VICTIM_PATTERNS):
        return True
    if (_has(value, REPRESSION_VICTIMS)
            and _has(value, ('арест', 'задерж', 'уголовн.*дел', 'сизо', 'колони', 'преслед'))):
        return True
    # An official's award, public statement or career is not a casualty report.
    # This never overrides the victim/medical/repression guard above.
    lead = re.split(r'\n\s*\n|\n', text.strip(), maxsplit=1)[0]
    focal = f'{title} ' + ' '.join(re.split(r'(?<=[.!?])\s+', lead)[:2])[:700]
    if _has(focal, ('обстрел', 'атак', 'катастроф', 'пожар', 'горит', 'эвакуац', 'насили', 'насиль', 'авари')):
        return True
    base = classify_mode(title, text)
    if base != RewriteMode.HARD_NEWS:
        return False
    # A public actor mentioned only in background must not turn an arrest into a joke.
    return not (_has(title, PUBLIC_TARGETS) and _has(focal, BUREAUCRATIC_TARGET))


def editorial_mode(title: str, text: str = '', style: str | None = None) -> RewriteMode:
    style = (style or os.getenv('EDITORIAL_STYLE', 'satirical')).strip().lower()
    if style != 'satirical':
        return classify_mode(title, text)
    return RewriteMode.HARD_NEWS if requires_serious_tone(title, text) else RewriteMode.SATIRICAL


def satirical_tail(body: str) -> str | None:
    """Reuse the already fact-validated final remark; never invent a second story."""
    paragraphs = [part.strip() for part in re.split(r'\n\s*\n', body) if part.strip()]
    if len(paragraphs) < 2:
        return None
    tail = paragraphs[-1]
    # Calculations, quotes and event details belong in evidence, not an extra punchline.
    if not 25 <= len(tail) <= 320 or re.search(r'\d|https?://|«|»', tail):
        return None
    return tail
