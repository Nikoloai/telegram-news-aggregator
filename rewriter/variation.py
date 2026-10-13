"""Small, deterministic repetition guard; not a second LLM editor."""
from __future__ import annotations

import html
import re
from difflib import SequenceMatcher

from aggregator.modes import RewriteMode


CATCHPHRASES = ('аналогов нет', 'своих не бросаем', 'своих не бросает', 'кадровая прачечная',
                'система снова победила', 'план перевыполнен', 'гойда')


def editorial_text(post_html: str) -> str:
    value = post_html.split('\n\n<a href=', 1)[0]
    value = re.sub(r'<blockquote>.*?</blockquote>', '', value, flags=re.S)
    value = re.sub(r'^([^\w<>]*)<b>[^<\n]{1,65}</b>\n', '', value)
    value = re.split(r'\n(?:<b>(?:[🟢🟡🔴🟠]|ОБНОВЛЕНО)|О том же сообщают:|Общий первоисточник:|Ранее:)', value, maxsplit=1)[0]
    value = re.sub(r'<b>Авторский (?:перевод|разбор) \(сатира\)</b>\n', '', value)
    return html.unescape(re.sub(r'<[^>]+>', '', value)).strip()


def _words(text: str) -> list[str]:
    return re.findall(r'[а-яa-z]+', text.lower().replace('ё', 'е'))


def _edges(text: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r'\n\s*\n', text) if part.strip()]
    return list(dict.fromkeys(paragraphs[:1] + paragraphs[-1:]))


def repeated_phrases(body: str, history: list[str], source: str = '') -> list[str]:
    normalized = ' '.join(_words(body))
    factual = ' '.join(_words(source))
    previous = [' '.join(_words(value)) for value in history]
    matches = []
    for phrase in CATCHPHRASES:
        if phrase in normalized and phrase not in factual and any(phrase in old for old in previous):
            matches.append(phrase)
    # Five-word phrases catch recurring constructions/metaphors with changed names.
    tokens = _words(body)
    for index in range(len(tokens) - 4):
        phrase = ' '.join(tokens[index:index + 5])
        if phrase not in factual and any(phrase in old for old in previous):
            matches.append(phrase)
    for edge in _edges(body):
        value = ' '.join(_words(edge))
        if len(_words(edge)) < 8 or value in factual:
            continue
        for old in history:
            if any(SequenceMatcher(None, value, ' '.join(_words(part))).ratio() >= .88
                   for part in _edges(old) if len(_words(part)) >= 8):
                matches.append(edge[:220])
                break
    return list(dict.fromkeys(matches))[:8]


def style_examples(history: list[str]) -> list[str]:
    return [edge[:180] for body in history[:30] for edge in _edges(body)]


def varied_rewrite(writer: object, article: object, mode: RewriteMode,
                   history: list[str], draft: object = None) -> str:
    setter = getattr(writer, 'set_style_memory', None)
    if callable(setter):
        setter(style_examples(history) if mode == RewriteMode.SATIRICAL else [])
    def generate() -> str:
        return writer.rewrite_commentary(article, draft) if draft else writer.rewrite(article, mode)
    body = generate()
    if mode != RewriteMode.SATIRICAL or not history:
        return body
    repeats = repeated_phrases(body, history, article.item.title + '\n' + article.text)
    if not repeats:
        return body
    if callable(setter):
        setter(style_examples(history) + ['Неприемлемый повтор: ' + phrase for phrase in repeats])
    body = generate()  # Exactly one retry, not an unbounded series of paid calls.
    if repeated_phrases(body, history, article.item.title + '\n' + article.text):
        raise ValueError('Repeated satirical construction after one retry')
    return body
