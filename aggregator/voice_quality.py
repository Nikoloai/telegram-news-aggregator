"""Fail-closed, lightweight checks for original satirical news copy.

These deterministic checks do not replace an independent LLM editorial review or
source/fact validation. They catch obvious template and verbatim-copy failures.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from aggregator.article import clean_html


@dataclass(frozen=True)
class SatireReview:
    decision: str
    reason: str
    revised_text: str = ""


def parse_satire_review(payload: str) -> SatireReview:
    """Unparseable or noncommittal reviewer output is never permission to publish."""
    try:
        data = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise ValueError("Satirical editorial review was not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Satirical editorial review must be a JSON object")
    decision = data.get("decision")
    reason = data.get("reason")
    revised_text = data.get("revised_text", "")
    if decision not in {"approve", "revise", "reject"}:
        raise ValueError("Invalid editorial decision")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Editorial review lacks a specific reason")
    if not isinstance(revised_text, str):
        raise ValueError("Editorial revision must be text")
    if decision == "revise" and not revised_text.strip():
        raise ValueError("Editorial revision is empty")
    if decision != "revise" and revised_text:
        raise ValueError("Only revise may supply replacement text")
    return SatireReview(decision, reason.strip()[:500], revised_text.strip())


GENERIC_CONCLUSIONS = (
    "это наглядно демонстрирует", "остаётся только надеяться", "остается только надеяться",
    "в очередной раз показывает", "очередной пример того, как",
    "время покажет", "только время покажет", "остаётся вопрос",
    "остается вопрос", "это вызывает вопросы", "неоднозначное решение",
)


def satirical_voice_issues(title: str, source: str, body: str) -> list[str]:
    """Obvious shortcomings only; no claim to measure wit by regex."""
    text = clean_html(body).strip()
    issues: list[str] = []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(text) < 190:
        issues.append("Текст слишком короток для авторской заметки")
    if len(paragraphs) < 2:
        issues.append("Нет связной авторской композиции из нескольких абзацев")
    normalized = re.sub(r"\s+", " ", text.lower().replace("ё", "е"))
    if any(phrase in normalized for phrase in GENERIC_CONCLUSIONS):
        issues.append("Шаблонная мораль или безадресная концовка")
    # The entire source newswire must not be reposted with a new emoji or heading.
    original = re.sub(r"\s+", " ", clean_html(f"{title}\n{source}").lower().replace("ё", "е"))
    meaningful = [p for p in paragraphs if len(p) > 70]
    copied = sum(re.sub(r"\s+", " ", p.lower().replace("ё", "е")) in original for p in meaningful)
    if meaningful and copied == len(meaningful):
        issues.append("Все содержательные абзацы скопированы из первоисточника")
    return issues
