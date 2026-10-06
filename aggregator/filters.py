from __future__ import annotations

import re
from collections.abc import Iterable

from .article import clean_html
from .models import FeedItem, TopicMatch


TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "war": (
        "войн", "украин", "обстрел", "атак", "дрон", "беспилот", "фронт",
        "боев", "военн", "погиб", "ранен", "плен", "ракет",
    ),
    "military": ("арм", "мобилизац", "контрактник", "военнослужащ", "потер"),
    "repression": (
        "политзаключ", "репресс", "задерж", "арест", "обыск", "сизо", "колони",
        "уголовн", "преследован", "приговор", "иноагент", "нежелательн",
    ),
    "media_pressure": ("журналист", "сми", "цензур", "блокиров", "роскомнадзор", "интернет"),
    "human_rights": ("прав человека", "пытк", "насили", "правозащит"),
    "state_policy": ("закон", "указ", "госдум", "правительств", "бюджет", "минфин"),
    "corruption": ("коррупц", "взятк", "расследован", "хищен", "офшор"),
    "propaganda": ("пропаганд", "фейк", "дезинформац"),
    "politics": ("выбор", "кремл", "президент", "оппозиц", "политическ"),
    "sanctions_economy": ("санкц", "экономик", "инфляц", "нефт", "газ", "налог", "ключев.*ставк"),
}

EXCLUDED_KEYWORDS = (
    "футбол", "хоккей", "теннис", "рецепт", "гороскоп", "мода", "светская хроника",
    "знаменитост", "премьера фильма", "музыкальный альбом",
)

FOREIGN_MARKERS = (
    "кндр", "северн.*коре", "инди", "китай", "франци", "германи", "сша", "америк",
    "израил", "газе", "палестин", "мексик", "африк", "британи",
)
CORE_CONTEXT = (
    "росси", "украин", "беларус", "кремл", "путин", "российск", "украинск",
    "москва", "санкт-петербург", "госдум", "роскомнадзор", "фсб", "мвд",
)


def _matches(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(rf"(?<![а-яёa-z0-9])(?:{pattern})", text, flags=re.IGNORECASE) for pattern in patterns)


class TopicFilter:
    """Rule-based MVP boundary with a stable interface for a future classifier."""

    def match(self, item: FeedItem) -> TopicMatch:
        haystack = " ".join(
            [item.title, clean_html(item.description), " ".join(item.categories)]
        ).lower()
        topics = [name for name, words in TOPIC_KEYWORDS.items() if _matches(haystack, words)]
        excluded = [word for word in EXCLUDED_KEYWORDS if word in haystack]
        unrelated_foreign = _matches(item.title, FOREIGN_MARKERS) and not _matches(item.title, CORE_CONTEXT)
        # A relevant political/public-interest signal wins over a generic excluded word.
        if unrelated_foreign:
            excluded.append("unrelated_foreign")
        return TopicMatch(
            accepted=bool(topics) and not unrelated_foreign and not (excluded and not topics),
            topics=topics,
            excluded_by=excluded,
        )
