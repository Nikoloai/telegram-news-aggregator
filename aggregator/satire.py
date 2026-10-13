"""Source-grounded candidates for three occasional satirical rubrics."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .article import clean_html
from .features import FeatureDraft, sentences
from .models import Article
from .modes import requires_serious_tone

SATIRE_RUBRICS = ('translation', 'excuse', 'career')
PUBLIC_ROLE = r'министр|губернатор|мэр|депутат|чиновник|дипломат|атташе|ведомств|администраци|правительств|кремл|пресс-секретар'
SPEECH = r'заяв\w*|сказ\w*|объясн\w*|назва\w*|по словам|комментир\w*'
FAILURE = r'не работает|не заработ|не заверш|сорван|срыв|провал|не выполн|не смог|увол|выслал|выслан'
JUSTIFICATION = r'оптимизац|эффективност|временн|техническ|обстоятельств|объективн|внешн|не завис|не от нас|санкц|планов\w* работ'
CAREER = r'назначен|назначил|возглавил|получил\w* должност|работает.*посольств|аккредитац'


def satire_week_window(now: datetime, zone: object = None) -> tuple[datetime, datetime]:
    if zone is None:
        name = os.getenv('POST_TIMEZONE', 'Europe/Moscow')
        try:
            zone = ZoneInfo(name)
        except ZoneInfoNotFoundError:
            if name != 'Europe/Moscow':
                raise
            zone = timezone(timedelta(hours=3))
    local = now.astimezone(zone)
    start = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(now.tzinfo), (start + timedelta(days=7)).astimezone(now.tzinfo)


def official_quote(article: Article, quote_pattern: str = '') -> str | None:
    for sentence in sentences(article.text):
        quotes = re.findall(r'«([^»]{12,240})»', sentence)
        attribution = re.sub(r'«[^»]+»', '', sentence)
        if (quotes and re.search(PUBLIC_ROLE, attribution, re.I) and re.search(SPEECH, attribution, re.I)
                and any(2 <= len(quote.split()) <= 35 for quote in quotes)
                and (not quote_pattern or any(re.search(quote_pattern, quote, re.I) for quote in quotes))
                and not re.search(r'не\s+(?:заяв|сказ)|не подтвержден|предположительно', sentence, re.I)):
            return sentence
    return None


def build_translation(article: Article) -> FeatureDraft | None:
    euphemism = r'оптимизац|повышен\w* эффективност|отрицательн\w* рост|корректиров|временн\w* трудност|стабилиз|не имеет аналог|планов\w* работ'
    statement = official_quote(article, euphemism)
    if not statement or requires_serious_tone(article.item.title, article.text):
        return None
    # Translate an actual quoted euphemism, not every ordinary statement.
    body = f'{clean_html(article.item.title)}\n\nБуквально по источнику: {statement}'
    return FeatureDraft('translation', body, (statement,))


def build_excuse(article: Article) -> FeatureDraft | None:
    statement = official_quote(article, JUSTIFICATION)
    if not statement or requires_serious_tone(article.item.title, article.text):
        return None
    if not re.search(JUSTIFICATION, statement, re.I):
        return None
    rows = sentences(article.text)
    index = rows.index(statement)
    # Keep the explanation tied to a failure in the SAME source/nearby passage.
    failure = next((row for row in rows[max(0, index - 1):index + 2]
                    if re.search(FAILURE, row, re.I)), None)
    if not failure:
        return None
    # Adjacency alone is insufficient: the explanation must name the same object
    # or explicitly refer to the failure it explains.
    if failure != statement:
        shared_object = set(re.findall(r'«([^»]{2,60})»', failure)) & set(re.findall(r'«([^»]{2,60})»', statement))
        if not shared_object and not re.search(r'объясн\w*.*(?:сбой|срыв|провал|задерж)|назва\w*.*(?:сбой|срыв|провал)|причин\w*.*(?:сбо|срыв|провал)', statement, re.I):
            return None
    # Two unrelated project names in the passage are not a verified contradiction.
    objects = re.findall(r'(?:сервис\w*|проект\w*|платформ\w*)\s+«([^»]+)»', statement + ' ' + failure, re.I)
    if len(set(objects)) > 1:
        return None
    body = f'{clean_html(article.item.title)}'
    if failure != statement:
        body += f'\n\nЧто произошло по источнику: {failure}'
    body += f'\n\nОфициальное объяснение: {statement}'
    evidence = tuple(dict.fromkeys((failure, statement)))
    return FeatureDraft('excuse', body, evidence)


def _people(text: str) -> set[tuple[str, str]]:
    names = re.findall(r'(?=\b([А-ЯЁ][а-яё]{2,})\s+([А-ЯЁ][а-яё]{2,})\b)', text)
    stop = {'российский', 'российского', 'совет', 'совета', 'глава', 'главой', 'главы', 'министр',
            'министра', 'министерство', 'администрация', 'правительство', 'госдума', 'посольство',
            'теперь', 'ранее', 'после', 'россии', 'московской', 'омской', 'сербии', 'самарской'}
    def stem(name: str) -> str:
        value = name.lower().replace('ё', 'е')
        if len(value) > 4:
            value = re.sub(r'(?:ом|ем|а|я|у|ю|е|ы|и|й|ь)$', '', value)
        return value
    return {(stem(first), stem(last))
            for first, last in names if first.lower() not in stop and last.lower() not in stop}


def build_career(article: Article) -> FeatureDraft | None:
    if requires_serious_tone(article.item.title, article.text):
        return None
    rows = sentences(article.text)
    for current in rows:
        if (not re.search(CAREER, current, re.I) or not re.search(PUBLIC_ROLE, current, re.I)
                or re.search(r'может|планир|ожидает|не\s+(?:назнач|возглав|работает)|предполож', current, re.I)):
            continue
        people = _people(current)
        if not people:
            continue
        for previous in rows:
            if previous == current or not re.search(FAILURE, previous, re.I):
                continue
            if not people & _people(previous):
                continue  # No fuzzy surname-only match or ambiguous pronoun resolution.
            if not re.search(r'\b20\d{2}\b|\bранее\b|\bпрежде\b|\bпосле\b', previous, re.I):
                continue
            old_years = re.findall(r'\b20\d{2}\b', previous)
            new_years = re.findall(r'\b20\d{2}\b', current)
            if old_years and new_years and max(map(int, old_years)) > min(map(int, new_years)):
                continue
            body = (f'{clean_html(article.item.title)}\n\nРаньше, по источнику: {previous}'
                    f'\n\nНовое назначение или работа: {current}'
                    '\n\nПоследовательность событий не доказывает, что назначение стало наградой за провал.')
            return FeatureDraft('career', body, (previous, current))
    return None


def select_satire_feature(article: Article, preferred: str, weekly_count: int,
                         excuse_count: int) -> FeatureDraft | None:
    if weekly_count >= 2:
        return None
    builders = {'translation': build_translation, 'excuse': build_excuse, 'career': build_career}
    order = ('excuse', 'career', 'translation') if preferred == 'auto' else (preferred,)
    for name in order:
        if name not in builders or (name == 'excuse' and excuse_count):
            continue
        draft = builders[name](article)
        if draft:
            return draft
    return None
