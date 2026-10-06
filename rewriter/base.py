from __future__ import annotations

from abc import ABC, abstractmethod

from aggregator.models import Article
from aggregator.modes import RewriteMode


class Rewriter(ABC):
    @abstractmethod
    def rewrite(self, article: Article, mode: RewriteMode) -> str:
        raise NotImplementedError
