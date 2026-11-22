from __future__ import annotations

import re
from collections.abc import Iterable

from .article import clean_html
from .models import FeedItem, TopicMatch


TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "war": (
        "войн", "украин", "обстрел", "атак", "дрон", "беспилот", "фронт",
        "боев", "военн", "погиб", "ранен", "плен", "ракет",
    ),
    "military": ("арм", "мобилизац", "контрактник", "военнослужащ", "потер"),
    "repression": (
        "политзаключ", "репресс", "задерж", "арест", "обыск", "сизо", "колони",
        "уголовн", "преследован", "приговор", "иноагент", "нежелательн",
    ),
    "media_pressure": ("журналист", "сми", "цензур", "блокиров", "роскомнадзор", "интернет"),
    "public_safety": ("эпидем", "отравлен", "заражен", "санитарн", "авари", "дтп", "сгорел", "отключ", "эвакуац", "пожар",
                      r"воздушн\w*\s+тревог", r"беспилотн\w*\s+опасност", r"(?:опасност|угроз)\w*\s+(?:атак\w*\s+)?(?:бпла|беспилот)",
                      "отключен.*(?:свет|вод|тепл|электр)", "крематор"),
    "human_rights": ("прав человека", "пытк", "насили", "правозащит"),
    "state_policy": ("закон", "указ", "госдум", "правительств", "бюджет", "минфин", "чиновник", "ведомств", "минцифр", "минстро",
                     "администрац", "госзакуп", "муниципаль", "депутат", "губернатор",
                     "землепольз", "градостро", "госконтракт"),
    "corruption": ("коррупц", "взятк", "расследован", "хищен", "офшор"),
    "propaganda": ("пропаганд", "фейк", "дезинформац", "импортозамещ", "аналогов нет"),
    "politics": ("выбор", "кремл", "президент", "оппозиц", "политическ"),
    "sanctions_economy": ("санкц", "экономик", "инфляц", "нефт", "газ", "налог", "ключев.*ставк",
                          "тариф", "жкх", "плат.*парков", "плат.*коммуналь", "стоимост.*проезд"),
}

EXCLUDED_KEYWORDS = (
    "футбол", "хоккей", "теннис", "рецепт", "гороскоп", "мода", "светская хроника",
    "знаменитост", "премьера фильма", "музыкальный альбом",
)

RUSSIA_CONTEXT = (
    "росси", "российск", "кремл", "путин", "рф", "москв", "санкт-петербург",
    "госдум", "совет федерации", "роскомнадзор", "росгвард", "фсб", "мвд",
    "минобороны рф", "минфин росс", "следственн.*комитет", "рубл", "чечн", "дагестан",
    "татарстан", "новосибирск", "белгород", "брянск", "курск", "сочи", "калининград",
)


def _matches(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(rf"(?<![а-яёa-z0-9])(?:{pattern})", text, flags=re.IGNORECASE) for pattern in patterns)


def kaliningrad_context(text: str) -> bool:
    return _matches(text, (
        r'калининград',
        # Geographic form, not a blanket match on the Baltic Sea or a surname.
        r'(?:в|на|над|под|у|из|возле|около)\s+(?:городе?\s+)?балтийск(?:е|а|у|ом)?\b(?!\s+(?:мор|флот|побереж))',
        r'(?:атак\w*|обстрел\w*)\s+(?:город\s+)?балтийск(?:е|а|у|ом)?\b(?!\s+(?:мор|флот|побереж))',
        r'(?:андре[йяю]|андреем|андрее)\s+(?:михайлович\w*\s+)?кропоткин',
        r'(?:евгени[йяю]|евгением|евгении)\s+(?:владимирович\w*\s+)?верхолаз',
        r'(?:алексе[йяю]|алексеем|алексее)\s+(?:сергеевич\w*\s+)?беспрозванных',
        r'антон\w*\s+гендриксон',
    ))


# Incidents are not the editorial core. A routine local mishap belongs in this
# channel only if it has substantial consequences or public accountability.
ROUTINE_INCIDENT_TITLE = re.compile(
    r'дтп|авари|пожар|возгоран|задымлен|прорыв|порыв труб|'
    r'отключени|без (?:света|воды|отопления)|сгорел|столкнул|'
    r'сбил[аи]?|дорожн.*происшеств|упал[аи]? дерев',
    re.IGNORECASE,
)
MAJOR_INCIDENT_IMPACT = re.compile(
    r'массов|крупн|масштабн|чрезвычайн|режим чс|эвакуац|'
    r'десятк|сотн|тысяч|многоквартирн.*(?:без|остал)|'
    r'(?:город|район|несколько районов|несколько населенных пунктов).{0,80}'
    r'(?:без света|без воды|без отопления|обесточ|отключ)|'
    r'торгов.*центр|больниц|школ|детск.*сад|'
    r'(?:(?:1[0-9]|[2-9][0-9]|[1-9][0-9]{2,})\s+'
    r'(?:человек|жител|погиб|пострада|ранен))',
    re.IGNORECASE,
)
PUBLIC_ACCOUNTABILITY = re.compile(
    r'халатност|нарушени.*(?:норм|правил|требован)|'
    r'скрыл[иа]?|фальсифиц|расследован|суд|провер[кя]|'
    r'коррупц|взятк|госконтракт|контракт|'
    r'неисправност.*(?:предупреждал|сообщал)|'
    r'власт.*(?:игнорировал|не реагировал)',
    re.IGNORECASE,
)


def is_routine_incident(title: str, body: str, topics: list[str]) -> bool:
    """Keep large incidents and material accountability; drop minor local mishaps."""
    if not ROUTINE_INCIDENT_TITLE.search(title):
        return False
    if set(topics).intersection({'war', 'military', 'repression', 'corruption'}):
        return False
    return not (MAJOR_INCIDENT_IMPACT.search(f'{title} {body}')
                or PUBLIC_ACCOUNTABILITY.search(f'{title} {body}'))


class TopicFilter:
    """Rule-based MVP boundary with a stable interface for a future classifier."""

    def match(self, item: FeedItem) -> TopicMatch:
        haystack = " ".join(
            [item.title, clean_html(item.description), " ".join(item.categories)]
        ).lower()
        topics = [name for name, words in TOPIC_KEYWORDS.items() if _matches(haystack, words)]
        excluded = [word for word in EXCLUDED_KEYWORDS if word in haystack]
        regional = kaliningrad_context(haystack)
        russia_related = _matches(haystack, RUSSIA_CONTEXT) or regional
        # A relevant political/public-interest signal wins over a generic excluded word.
        if not russia_related:
            excluded.append("not_russia_related")
        accepted = bool(topics) and russia_related and not (excluded and not topics)
        if accepted and is_routine_incident(item.title, clean_html(item.description), topics):
            accepted = False
            excluded.append("routine_minor_incident")
        if accepted and regional:
            topics.append('kaliningrad')
        return TopicMatch(
            accepted=accepted,
            topics=topics,
            excluded_by=excluded,
        )
