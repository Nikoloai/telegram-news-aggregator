from __future__ import annotations

import re
from collections import Counter
from os.path import commonprefix

from .models import ValidationResult

NUMBER_RE = re.compile(r"(?<![\w])\d+(?:[.,]\d+)?(?:\s?(?:%|₽|руб(?:лей|ля|ль)?|доллар(?:ов|а)?|евро))?", re.I)
DATE_RE = re.compile(
    r"\b(?:\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?|\d{1,2}\s+"
    r"(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
    r"(?:\s+\d{4}(?:\s+года)?)?)\b",
    re.I,
)
ENTITY_RE = re.compile(r"\b(?:[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){1,3}|[А-ЯA-ZЁ]{2,}(?:-[А-ЯA-ZЁ]+)?)\b")
ENTITY_STOP = {"Что здесь", "Источник", "HARD NEWS", "ANALYSIS", "IRONIC"}


def _normalized_counter(pattern: re.Pattern[str], text: str) -> Counter[str]:
    return Counter(re.sub(r"\s+", " ", value.lower()).replace(",", ".") for value in pattern.findall(text))


def _entities(text: str) -> set[str]:
    return {value.lower().replace("ё", "е") for value in ENTITY_RE.findall(text) if value not in ENTITY_STOP}


def _entity_supported(entity: str, source_text: str) -> bool:
    """Allow common Russian case endings while still rejecting new names."""
    source_words = re.findall(r"[а-яёa-z]{3,}", source_text.lower().replace("ё", "е"))
    entity_words = re.findall(r"[а-яёa-z]{3,}", entity)
    return all(
        any(
            len(commonprefix((word, source_word))) >= min(4, len(word))
            for source_word in source_words
        )
        for word in entity_words
    )


class FactPreservationValidator:
    """Rejects unsupported facts; it does not claim to fact-check the source."""

    def validate(self, source_text: str, generated: str, title: str = "") -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        source_numbers = _normalized_counter(NUMBER_RE, source_text)
        generated_numbers = _normalized_counter(NUMBER_RE, generated)
        unsupported_numbers = list((generated_numbers - source_numbers).elements())
        if unsupported_numbers:
            errors.append("Новые или изменённые числа: " + ", ".join(sorted(set(unsupported_numbers))))

        source_dates = _normalized_counter(DATE_RE, source_text)
        generated_dates = _normalized_counter(DATE_RE, generated)
        unsupported_dates = list((generated_dates - source_dates).elements())
        if unsupported_dates:
            errors.append("Новые или изменённые даты: " + ", ".join(sorted(set(unsupported_dates))))

        generated_entities = _entities(generated)
        unsupported_entities = {
            entity for entity in generated_entities if not _entity_supported(entity, source_text)
        }
        if unsupported_entities:
            errors.append("Новые имена или организации: " + ", ".join(sorted(unsupported_entities)))

        key_entities = _entities(title)
        missing_key = {entity for entity in key_entities if not _entity_supported(entity, generated)}
        if missing_key:
            warnings.append("В результате пропущены ключевые сущности: " + ", ".join(sorted(missing_key)))
        return ValidationResult(valid=not errors, errors=errors, warnings=warnings)
