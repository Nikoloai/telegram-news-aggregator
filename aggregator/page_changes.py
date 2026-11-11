"""Compare independently fetched versions of already published source pages."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from difflib import SequenceMatcher

from .article import ArticleFetcher, clean_html
from .dedupe import normalize_title, normalize_url
from .features import sentences
from .models import Article, FeedItem
from .readers import source_url
from .storage import Storage

CONTEXT = re.compile(r'срок|стоимост|бюджет|тариф|обещ|планир|строит|ремонт|заверш|откры|запуст|выплат', re.I)
PROMISE = re.compile(r'\b(?:не\s+)?(?:обещ\w*|планир\w*|обяз\w*|поруч\w*|намерен\w*|будет|будут|долж\w*)\b', re.I)
FACT = re.compile(r'(\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?)\s*'
                  r'(млн|миллион\w*|млрд|миллиард\w*|тыс(?:яч\w*)?\.?)?\s*'
                  r'(руб\w*\.?|доллар\w*|евро|процент\w*|%)?', re.I)
MONTH = re.compile(r'\b(?:январ\w*|феврал\w*|март\w*|апрел\w*|ма[йяе]|июн\w*|июл\w*|'
                   r'август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)\b', re.I)
NOTICE = re.compile(r'(?:^|\n)\s*(?:уточнение|исправление|обновлено|upd)\b', re.I)


def normalized(text: str) -> str:
    return re.sub(r'\s+', ' ', text.lower().replace('ё', 'е')).strip()


def facts(text: str) -> tuple[list[tuple[Decimal, str]], list[str]]:
    values = []
    for match in FACT.finditer(text):
        value = Decimal(re.sub(r'\s', '', match[1]).replace(',', '.'))
        scale = (match[2] or '').lower()
        if scale.startswith(('млрд', 'миллиард')):
            value *= 1000000000
        elif scale.startswith(('млн', 'миллион')):
            value *= 1000000
        elif scale:
            value *= 1000
        unit = (match[3] or '').lower()
        if unit.startswith('руб'):
            unit = 'руб'
        elif unit.startswith('доллар'):
            unit = 'доллар'
        elif unit.startswith('процент'):
            unit = '%'
        values.append((value, unit))
    return values, MONTH.findall(text.lower())


def changed_fragment(before: str, after: str) -> tuple[str, str] | None:
    if NOTICE.search(after):
        return None
    old, new = sentences(before), sentences(after)
    diff = SequenceMatcher(None, [normalized(s) for s in old], [normalized(s) for s in new], autojunk=False)
    for tag, left, right, start, end in diff.get_opcodes():
        if tag == 'delete' and right - left == 1:
            previous = old[left]
            if (CONTEXT.search(previous) and PROMISE.search(previous)
                    and normalize_title(previous) not in normalize_title(after)):
                return previous, ''
        if tag != 'replace' or right - left != 1 or end - start != 1:
            continue
        previous, current = old[left], new[start]
        if not CONTEXT.search(previous + ' ' + current):
            continue
        if SequenceMatcher(None, normalized(previous), normalized(current), autojunk=False).ratio() < 0.65:
            continue
        number_change = facts(previous) != facts(current)
        commitment_change = bool(PROMISE.search(previous) or PROMISE.search(current)) and (
            PROMISE.findall(previous.lower()) != PROMISE.findall(current.lower()))
        if number_change or commitment_change:
            return previous, current
    return None


@dataclass(frozen=True)
class PageChange:
    version_id: int
    item: FeedItem
    source_url: str
    body: str
    evidence: str
    original_message_id: int | None


class UncertainPageSend(RuntimeError):
    """A durable send intent needs reconciliation before the same card can be retried."""


class PageMonitor:
    def __init__(self, storage: Storage):
        self.db = storage.connection

    def enroll(self, history: list[object], sources: list[dict[str, object]], now: datetime) -> None:
        cutoff = (now - timedelta(days=30)).isoformat()
        candidates = {}
        for row in history:
            url = str(row['canonical_url'] or row['url'])
            allowed = source_url(url, sources)
            if (not allowed or allowed[0] != normalize_url(url) or row['updated_at'] < cutoff
                    or row['rubric'] in {'longread', 'changes', 'correction', 'weekly'}
                    or not CONTEXT.search(str(row['title']) + ' ' + str(row['content'] or ''))):
                continue
            candidates.setdefault(allowed[0], row)
            if len(candidates) == 30:
                break
        with self.db:
            self.db.execute("UPDATE page_versions SET status='EXPIRED' WHERE status='PENDING' AND first_seen<?",
                            ((now - timedelta(hours=48)).isoformat(),))
            for watch in self.db.execute('SELECT url FROM page_watches').fetchall():
                if not source_url(watch['url'], sources):
                    self.db.execute('DELETE FROM page_watches WHERE url=?', (watch['url'],))
            self.db.execute("DELETE FROM page_watches WHERE started_at<? AND url NOT IN "
                            "(SELECT url FROM page_versions WHERE status IN ('SENDING','REVIEW_PENDING'))", (cutoff,))
            if candidates:
                placeholders = ','.join('?' for _ in candidates)
                self.db.execute(f'DELETE FROM page_watches WHERE url NOT IN ({placeholders}) '
                                "AND url NOT IN (SELECT url FROM page_versions WHERE status IN "
                                "('PENDING','SENDING','REVIEW_PENDING'))", tuple(candidates))
            self.db.execute('DELETE FROM page_versions WHERE url NOT IN (SELECT url FROM page_watches) '
                            "AND status NOT IN ('SENDING','REVIEW_PENDING') AND id NOT IN "
                            "(SELECT previous_id FROM page_versions WHERE previous_id IS NOT NULL "
                            "AND status IN ('SENDING','REVIEW_PENDING'))")
            count = self.db.execute('SELECT COUNT(*) FROM page_watches').fetchone()[0]
            for url, row in candidates.items():
                if count >= 30:
                    break
                inserted = self.db.execute(
                    'INSERT OR IGNORE INTO page_watches (url,title,source,message_id,started_at,next_check) '
                    'VALUES (?,?,?,?,?,?)',
                    (url, str(row['title']), str(row['source']), row['telegram_message_id'],
                     str(row['updated_at']), now.isoformat()))
                count += inserted.rowcount

    def observe(self, article: Article, now: datetime) -> None:
        url = normalize_url(article.item.url)
        text = article.page_text or article.text
        with self.db:
            updated = self.db.execute('UPDATE page_watches SET next_check=? WHERE url=?',
                                       ((now + timedelta(hours=6)).isoformat(), url))
            if not updated.rowcount:
                return
            watch = self.db.execute('SELECT * FROM page_watches WHERE url=?', (url,)).fetchone()
            if not article.page_verified or not 120 <= len(text) <= 18000:
                return
            previous = self.db.execute('SELECT * FROM page_versions WHERE id=?',
                                       (watch['latest_version'],)).fetchone()
            if previous and normalized(previous['body_text']) == normalized(text):
                self.db.execute('UPDATE page_versions SET last_seen=? WHERE id=?',
                                (now.isoformat(), previous['id']))
                if previous['status'] == 'PENDING' and NOTICE.search(article.page_title):
                    self.db.execute("UPDATE page_versions SET status='BASELINE' WHERE id=?", (previous['id'],))
                return
            if previous:
                similarity = SequenceMatcher(None, normalized(previous['body_text']).split(),
                                             normalized(text).split(), autojunk=False).ratio()
                if similarity < 0.6:
                    return  # A login page or a replacement article cannot establish a revision.
            reference = previous
            if previous and previous['status'] == 'PENDING':
                reference = self.db.execute('SELECT * FROM page_versions WHERE id=?',
                                            (previous['previous_id'],)).fetchone()
            fragment = changed_fragment(reference['body_text'], text) if reference else None
            status = 'PENDING' if fragment and not NOTICE.search(article.page_title) else 'BASELINE'
            signature = hashlib.sha256(f'{url}\n{now.isoformat()}\n{text}'.encode()).hexdigest()
            event_url = url + '?gvozdika_revision=' + signature
            self.db.execute("UPDATE page_versions SET status='SUPERSEDED' WHERE url=? AND status='PENDING'", (url,))
            inserted = self.db.execute(
                'INSERT INTO page_versions (url,body_text,first_seen,last_seen,previous_id,status,event_url) '
                'VALUES (?,?,?,?,?,?,?)', (url, text, now.isoformat(), now.isoformat(),
                                          reference['id'] if reference else None, status, event_url))
            self.db.execute('UPDATE page_watches SET latest_version=? WHERE url=?', (inserted.lastrowid, url))
            # Keep twelve recent versions and the earlier evidence needed by pending cards.
            self.db.execute("DELETE FROM page_versions WHERE url=? AND id NOT IN "
                            "(SELECT id FROM page_versions WHERE url=? ORDER BY id DESC LIMIT 12) "
                            "AND status NOT IN ('PENDING','SENDING','REVIEW_PENDING') AND id NOT IN "
                            "(SELECT previous_id FROM page_versions WHERE previous_id IS NOT NULL AND "
                            "(status IN ('PENDING','SENDING','REVIEW_PENDING') OR id=?))",
                            (url, url, inserted.lastrowid))

    def scan(self, fetcher: ArticleFetcher, now: datetime) -> None:
        due = self.db.execute('SELECT * FROM page_watches WHERE next_check<=? ORDER BY next_check,url LIMIT 3',
                              (now.isoformat(),)).fetchall()
        for row in due:
            article = fetcher.fetch(FeedItem(row['source'], row['title'], row['url']))
            self.observe(article, now)

    def pending(self, now: datetime, reconcile: bool = True) -> PageChange | None:
        # Reconcile a crash after the existing publication/review record was committed.
        if reconcile:
            with self.db:
                self.db.execute("UPDATE page_versions SET status='PUBLISHED' WHERE event_url IN "
                                "(SELECT url FROM articles WHERE status='PUBLISHED')")
                self.db.execute("UPDATE page_versions SET status='REJECTED' WHERE event_url IN "
                                "(SELECT url FROM articles WHERE status='REJECTED')")
        row = self.db.execute(
            "SELECT v.*,w.title,w.source,w.message_id,p.body_text AS old_body,p.last_seen AS old_seen "
            "FROM page_versions v JOIN page_watches w ON w.latest_version=v.id "
            "JOIN page_versions p ON p.id=v.previous_id WHERE v.status='PENDING' AND v.first_seen>=? "
            "ORDER BY v.first_seen,v.id LIMIT 1", ((now-timedelta(hours=48)).isoformat(),)).fetchone()
        if not row:
            return None
        fragment = changed_fragment(row['old_body'], row['body_text'])
        if not fragment:
            return None
        before, after = fragment
        zone = timezone(timedelta(hours=3))
        old_time = datetime.fromisoformat(row['old_seen']).astimezone(zone).strftime('%d.%m.%Y %H:%M МСК')
        new_time = datetime.fromisoformat(row['first_seen']).astimezone(zone).strftime('%d.%m.%Y %H:%M МСК')
        current = f'«{after}»' if after else 'Этот фрагмент отсутствует в сохранённой новой версии.'
        body = (f"{clean_html(row['title'])}\n\nСохранено {old_time}\n«{before}»"
                f'\n\nПроверено {new_time}\n{current}')
        evidence = f"{row['title']}\n{old_time}\n{row['old_body']}\n{new_time}\n{row['body_text']}"
        item = FeedItem(row['source'], row['title'], row['event_url'], published_at=now)
        return PageChange(row['id'], item, row['url'], body, evidence, row['message_id'])

    def mark(self, change: PageChange, status: str) -> bool:
        with self.db:
            result = self.db.execute("UPDATE page_versions SET status=? WHERE id=? AND status='PENDING'",
                                     (status, change.version_id))
        return result.rowcount == 1

    def claim(self, event_url: str, expected_status: str = 'PENDING') -> bool:
        with self.db:
            result = self.db.execute("UPDATE page_versions SET status='SENDING' WHERE event_url=? AND status=?",
                                     (event_url, expected_status))
        return result.rowcount == 1

    def finish(self, change: PageChange, status: str) -> None:
        with self.db:
            self.db.execute('UPDATE page_versions SET status=? WHERE id=?', (status, change.version_id))
