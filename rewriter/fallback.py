from __future__ import annotations

import re

from aggregator.article import clean_html
from aggregator.models import Article
from aggregator.modes import RewriteMode

from .base import Rewriter


class FallbackRewriter(Rewriter):
    def rewrite(self, article: Article, mode: RewriteMode) -> str:
        title = clean_html(article.item.title).rstrip(".")
        raw = clean_html(article.item.description) or clean_html(article.text)
        raw = re.sub(r"(?:Читайте также|Подробнее)\s*:?.*$", "", raw, flags=re.I)
        sentences = re.split(r"(?<=[.!?])\s+", raw)
        summary = " ".join(sentence for sentence in sentences[:3] if sentence).strip()
        if len(summary) > 850:
            summary = summary[:847].rsplit(" ", 1)[0] + "…"
        significant = bool(re.search(r"\b(?:срочно|принял|ввел|объявил|запрет|санкц|отставк)", title, re.I))
        lead = f"⚡ {title}" if mode != RewriteMode.HARD_NEWS and significant else title
        parts = [lead, summary] if summary and summary.lower() != title.lower() else [lead]
        return "\n\n".join(parts)
