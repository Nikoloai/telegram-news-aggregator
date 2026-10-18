"""Approved longreads only: Telegraph page + ONE Telegram announcement.

No model generation here. Ambiguous remote outcomes stop automatic retries.
Account credentials in the public state branch are encrypted, never logged.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from cryptography.fernet import Fernet

from .models import FeedItem
from .schedule import spacing_ready
from .storage import Storage


def moscow_zone():
    try:
        return ZoneInfo('Europe/Moscow')
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=3), name='Europe/Moscow')


def inline_nodes(text: str) -> list:
    pattern = re.compile(r'\[([^\]]+)\]\((https://[^\s)]+)\)|\*\*([^*]+)\*\*|\*([^*]+)\*')
    result = []
    position = 0
    for match in pattern.finditer(text):
        if match.start() > position:
            result.append(text[position:match.start()])
        if match.group(1):
            result.append({'tag': 'a', 'attrs': {'href': match.group(2)}, 'children': [match.group(1)]})
        else:
            result.append({'tag': 'strong' if match.group(3) else 'em',
                           'children': [match.group(3) or match.group(4)]})
        position = match.end()
    if position < len(text):
        result.append(text[position:])
    return result


def render_article(markdown: str) -> tuple[str, list]:
    blocks = re.split(r'\n\s*\n', markdown.strip())
    if not blocks or not blocks[0].startswith('# '):
        raise ValueError('Longread must start with a title')
    title = blocks.pop(0)[2:].strip()
    if not 1 <= len(title) <= 256:
        raise ValueError('Invalid article title length')
    content = []
    for block in blocks:
        if block.startswith('## '):
            tag, text = 'h3', block[3:]
        elif block.startswith('### '):
            tag, text = 'h4', block[4:]
        else:
            tag, text = 'p', block.replace('\n', ' ')
        content.append({'tag': tag, 'children': inline_nodes(text)})
    if len(json.dumps(content, ensure_ascii=False).encode()) > 64000:
        raise ValueError('Telegraph content exceeds 64 KB')
    return title, content


def load_queue(root: Path) -> list[dict]:
    queue = json.loads((root / 'queue.json').read_text(encoding='utf-8'))
    slugs = set()
    for item in queue:
        slug = item['slug']
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', slug) or slug in slugs:
            raise ValueError('Invalid or duplicate longread slug')
        slugs.add(slug)
        for field in ('article', 'announcement'):
            path = (root / item[field]).resolve()
            if root.resolve() not in path.parents or not path.is_file():
                raise ValueError('Longread path outside content root or missing')
        due = datetime.fromisoformat(item['publish_at'])
        if due.tzinfo is None:
            raise ValueError('Publication time must have a timezone')
        title, _ = render_article((root / item['article']).read_text(encoding='utf-8'))
        item['_title'] = title
        teaser = (root / item['announcement']).read_text(encoding='utf-8').strip()
        if not 100 <= len(teaser) <= 1500 or 'http' in teaser:
            raise ValueError('Announcement must be short and must not contain placeholder links')
    return sorted(queue, key=lambda item: datetime.fromisoformat(item['publish_at']))


def eligible(item: dict, now: datetime, immediate: bool = False) -> bool:
    if item.get('approved') is not True:
        return False
    due = datetime.fromisoformat(item['publish_at'])
    if now < due:
        return False
    if immediate and item.get('publish_immediately') is True:
        return True
    local = now.astimezone(moscow_zone())
    return 8 <= local.hour < 20 and now - due <= timedelta(days=2)


def announcement_html(title: str, teaser: str, url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc != 'telegra.ph' or not parsed.path.strip('/'):
        raise ValueError('Unexpected article URL')
    return (f'📚 <b>По документам</b>\n\n<b>{html.escape(title)}</b>\n\n'
            f'{html.escape(teaser.strip())}\n\n'
            f'<a href="{html.escape(url, quote=True)}">Читать полный разбор</a>')


class RemoteError(RuntimeError):
    pass


def safe_post(url: str, payload: dict) -> dict:
    # Never put exception repr, request URLs with bot credentials, or raw payloads in logs.
    try:
        kwargs = {'data': payload} if url.startswith('https://api.telegra.ph/') else {'json': payload}
        response = requests.post(url, **kwargs, timeout=40)
        if response.status_code != 200:
            raise RemoteError(f'Remote HTTP {response.status_code}')
        data = response.json()
        if not data.get('ok') or not isinstance(data.get('result'), dict):
            raise RemoteError('Remote API rejected the request')
        return data['result']
    except (requests.RequestException, ValueError):
        raise RemoteError('Remote response unavailable; manual reconciliation required') from None


class LongreadPublisher:
    def __init__(self, storage: Storage, bot_token: str, channel: str):
        self.storage = storage
        self.bot_token = bot_token
        self.channel = channel
        key = hashlib.sha256(b'longread-telegraph-v1\0' + bot_token.encode()).digest()
        self.cipher = Fernet(base64.urlsafe_b64encode(key))

    def account_token(self) -> str:
        encrypted = self.storage.get_meta('longreads:telegraph:encrypted_token')
        if encrypted:
            try:
                return self.cipher.decrypt(encrypted.encode()).decode()
            except Exception:
                raise RemoteError('Telegraph credential cannot be unlocked; token rotation requires recovery') from None
        # Intent prevents unbounded account creation after an uncertain response.
        if self.storage.get_meta('longreads:telegraph:account_intent'):
            raise RemoteError('Telegraph account outcome uncertain; manual reconciliation required')
        self.storage.set_meta('longreads:telegraph:account_intent', 'creating')
        result = safe_post('https://api.telegra.ph/createAccount', {
            'short_name': 'Gvozdika Documents', 'author_name': 'Гвоздика · По документам',
            'author_url': 'https://t.me/GVOZDIchKAAA'})
        token = result['access_token']
        self.storage.set_meta('longreads:telegraph:encrypted_token', self.cipher.encrypt(token.encode()).decode())
        self.storage.set_meta('longreads:telegraph:account_intent', 'created')
        return token

    def publish(self, item: dict, root: Path, now: datetime) -> dict:
        key = 'longread:' + item['slug']
        stored = json.loads(self.storage.get_meta(key) or '{}')
        if stored.get('status') == 'published':
            return stored
        if stored.get('status') in {'sending', 'page_creating'}:
            raise RemoteError('Previous remote outcome uncertain; do not retry automatically')
        article = (root / item['article']).read_text(encoding='utf-8')
        title, content = render_article(article)
        if not stored.get('url'):
            token = self.account_token()
            stored = {'status': 'page_creating', 'title': title}
            self.storage.set_meta(key, json.dumps(stored, ensure_ascii=False))
            page = safe_post('https://api.telegra.ph/createPage', {
                'access_token': token, 'title': title, 'content': json.dumps(content, ensure_ascii=False),
                'author_name': 'Гвоздика · По документам', 'author_url': 'https://t.me/GVOZDIchKAAA',
                'return_content': 'false'})
            stored.update(status='page_ready', url=page['url'], path=page['path'],
                          content_sha256=hashlib.sha256(article.encode()).hexdigest())
            self.storage.set_meta(key, json.dumps(stored, ensure_ascii=False))
        teaser = (root / item['announcement']).read_text(encoding='utf-8')
        post = announcement_html(title, teaser, stored['url'])
        if len(post) > 4000:
            raise ValueError('Telegram announcement too long')
        stored['status'] = 'sending'
        self.storage.set_meta(key, json.dumps(stored, ensure_ascii=False))
        sent = safe_post(f'https://api.telegram.org/bot{self.bot_token}/sendMessage', {
            'chat_id': self.channel, 'text': post, 'parse_mode': 'HTML',
            'link_preview_options': {'is_disabled': False, 'url': stored['url']}})
        message_id = int(sent['message_id'])
        # Publish receipt first; a crash must never resend an accepted announcement.
        stored.update(status='published', message_id=message_id, published_at=now.isoformat())
        self.storage.set_meta(key, json.dumps(stored, ensure_ascii=False))
        self.record_publication(stored, article, post, now)
        return stored

    def record_publication(self, receipt: dict, article: str = '', post: str = '', now: datetime | None = None) -> None:
        stamp = datetime.fromisoformat(receipt['published_at'])
        self.storage.save(FeedItem(source='Гвоздика · По документам', title=receipt['title'],
                                  url=receipt['url'], published_at=stamp), 'PUBLISHED', content=article,
                          rewrite_mode='HARD', topics=['longread'],
                          telegram_message_id=int(receipt['message_id']), post_html=post, rubric='longread')
        self.storage.connection.execute('UPDATE publications SET published_at=? WHERE telegram_message_id=?',
                                        (stamp.isoformat(), int(receipt['message_id'])))
        self.storage.connection.commit()


def publish_due(root: Path, storage: Storage, publisher: LongreadPublisher | None,
                now: datetime, immediate: bool = False, daily_limit: int = 10) -> dict | None:
    queue = load_queue(root)
    # Repair a possible receipt-to-counter gap before assessing the daily quota.
    for item in queue:
        receipt = json.loads(storage.get_meta('longread:' + item['slug']) or '{}')
        if receipt.get('status') == 'published':
            row = storage.connection.execute('SELECT 1 FROM publications WHERE telegram_message_id=?',
                                             (int(receipt['message_id']),)).fetchone()
            if row is None:
                if publisher is None:
                    raise RemoteError('Published receipt needs counter reconciliation before preview')
                publisher.record_publication(receipt)
    local = now.astimezone(moscow_zone())
    start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    if storage.count_published_between(start, start + timedelta(days=1)) >= daily_limit:
        return None
    for item in queue:
        receipt = json.loads(storage.get_meta('longread:' + item['slug']) or '{}')
        if receipt.get('status') in {'sending', 'page_creating'}:
            # Quarantine only this longread; never stop the ordinary news pipeline.
            print('Longread awaiting manual reconciliation:', item['slug'])
            continue
        if receipt.get('status') == 'published' or not eligible(item, now, immediate):
            continue
        if not (immediate and item.get('publish_immediately') is True):
            if not spacing_ready(now, storage.last_publication_at()):
                return None
        if publisher is None:
            return {'status': 'preview', 'slug': item['slug'], 'title': item['_title']}
        return publisher.publish(item, root, now)
    return None
