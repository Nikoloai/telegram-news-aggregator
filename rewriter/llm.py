from __future__ import annotations

import os

import requests

from aggregator.models import Article
from aggregator.modes import RewriteMode

from .base import Rewriter

SYSTEM_PROMPT = """Ты — редактор русскоязычного новостного Telegram-канала.
Пиши как опытный аналитик: спокойно, умно, ясно и интересно, современным естественным русским языком.
Подача должна быть нативной для Telegram: сильный информативный лид, короткие абзацы, высокая плотность фактов.
По ритму ориентируйся на лучшие короткие новостные каналы вроде ASTRA и «Ватного болота», но не копируй их формулировки, шутки или уникальный голос.
Символ ⚡ допустим один раз перед лидом для срочной или особенно значимой новости; не используй его механически.
Исходный материал — только источник фактов. Не копируй большие фрагменты дословно.
Всегда сохраняй имена, даты, цифры, географию, организации, атрибуцию и степень уверенности.
Слова «по предварительным данным», «по словам», «утверждает», «сообщает» и «предположительно» нельзя усиливать до установленного факта.
Не придумывай факты, цитаты, мотивы, мнения или причинно-следственные связи.
HARD_NEWS: только спокойный фактический тон, без иронии о пострадавших.
ANALYSIS: можно добавить раздел «Что здесь важно:» с 1–3 предложениями контекста, если он следует из материала.
IRONIC: максимум одна короткая сухая ироничная ремарка, только если противоречие есть в исходнике.
Формат: короткий заголовок/лид и 2–4 коротких абзаца. Не добавляй строку источника или URL — система сделает это сама.
Целевая длина основного текста: 400–1200 знаков."""


class LLMRewriter(Rewriter):
    def __init__(self, provider: str, api_key: str, model: str, base_url: str | None = None, timeout: int = 60):
        self.provider = provider.lower()
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "LLMRewriter":
        return cls(
            provider=os.getenv("LLM_PROVIDER", "openai"),
            api_key=os.environ["LLM_API_KEY"],
            model=os.getenv("LLM_MODEL", "gpt-4.1-mini"),
            base_url=os.getenv("LLM_BASE_URL"),
        )

    def rewrite(self, article: Article, mode: RewriteMode) -> str:
        if self.provider not in {"openai", "openai_compatible"}:
            raise ValueError(f"Неподдерживаемый LLM_PROVIDER: {self.provider}")
        source = article.text or article.item.description
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"Режим: {mode.value}\nЗаголовок: {article.item.title}\n\nИсходный материал:\n{source[:15000]}",
                    },
                ],
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return str(response.json()["choices"][0]["message"]["content"]).strip()
