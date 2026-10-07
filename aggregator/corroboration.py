from __future__ import annotations

from rapidfuzz import fuzz

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
