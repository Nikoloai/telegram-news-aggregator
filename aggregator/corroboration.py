from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from rapidfuzz import fuzz

from .article import clean_html
from .dedupe import GENERIC_WORDS, normalize_title
from .models import FeedItem


def stories_match(left: str, right: str, threshold: int = 76) -> bool:
    left_normalized = normalize_title(left)
    right_normalized = normalize_title(right)
    if fuzz.token_set_ratio(left_normalized, right_normalized) >= threshold:
        return True
    left_words = {
        word for word in left_normalized.split() if len(word) >= 6 and word not in GENERIC_WORDS
    }
    right_words = {
        word for word in right_normalized.split() if len(word) >= 6 and word not in GENERIC_WORDS
    }
    return len(left_words.intersection(right_words)) >= 3


def corroborating_sources(item: FeedItem, candidates: list[FeedItem]) -> list[str]:
    sources: list[str] = []
    for candidate in candidates:
        if candidate is item or candidate.source == item.source:
            continue
        if stories_match(item.title, candidate.title) and candidate.source not in sources:
            sources.append(candidate.source)
    return sources


@dataclass(frozen=True)
class SourceEvidence:
    sources: list[str]
    shared_origins: list[str]
    conflicts: list[str]


ORIGINS = {
    "ТАСС": r"тасс",
    "РИА Новости": r"риа(?: новости)?",
    "Минобороны": r"минобороны",
    "Росстат": r"росстат",
    "Следственный комитет": r"следственн\w* комитет",
    "Кремль": r"кремл\w*",
}


def attributed_origins(text: str) -> set[str]:
    # Require attribution, rather than treating every mentioned organization as a source.
    value = clean_html(text).lower()
    attribution = r"(?:по данным|по словам|со ссылкой на|сообщ\w*|заяв\w*|утвержд\w*)"
    return {
        name for name, pattern in ORIGINS.items()
        if re.search(rf"(?:{attribution})[^.!?]{{0,65}}(?:{pattern})|(?:{pattern})[^.!?]{{0,45}}(?:{attribution})", value)
    }


def source_evidence(item: FeedItem, candidates: list[FeedItem]) -> SourceEvidence:
    sources: list[str] = []
    origins = attributed_origins(f"{item.title} {item.description}")
    shared: set[str] = set()
    conflicts: list[str] = []
    for other in candidates:
        if other is item or other.source == item.source:
            continue
        if item.published_at and other.published_at:
            if abs(item.published_at - other.published_at) > timedelta(hours=36):
                continue
        if not stories_match(item.title, other.title):
            continue
        if other.source not in sources:
            sources.append(other.source)
        shared.update(origins & attributed_origins(f"{other.title} {other.description}"))
        left_numbers = set(re.findall(r"\b\d+\b", item.title))
        right_numbers = set(re.findall(r"\b\d+\b", other.title))
        if left_numbers and right_numbers and left_numbers != right_numbers:
            conflicts.append(f"Разные числа в заголовках: {item.source} / {other.source}")
        denial = lambda value: bool(re.search(r"\b(?:не было|не подтверд\w*|опроверг\w*)", value, re.I))
        if denial(item.title) != denial(other.title):
            conflicts.append(f"Расхождение утверждения и опровержения: {other.source}")
    return SourceEvidence(sources, sorted(shared), list(dict.fromkeys(conflicts)))
