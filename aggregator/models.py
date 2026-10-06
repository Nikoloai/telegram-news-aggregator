from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(slots=True)
class FeedItem:
    source: str
    title: str
    url: str
    description: str = ""
    published_at: datetime | None = None
    categories: list[str] = field(default_factory=list)
    canonical_url: str | None = None


@dataclass(slots=True)
class Article:
    item: FeedItem
    text: str
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class TopicMatch:
    accepted: bool
    topics: list[str]
    excluded_by: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
