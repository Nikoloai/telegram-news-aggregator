from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv import load_dotenv

from aggregator.article import ArticleFetcher
from aggregator.collector import FeedCollector
from aggregator.corroboration import corroborating_sources, stories_match
from aggregator.dedupe import Deduplicator, is_meaningful_update, normalize_title, normalize_url
from aggregator.editorial import (
    confirmation_level,
    extract_title_quote,
    is_correction,
    is_emergency,
    promise_result_publication,
    related_publication,
    rotate_items,
    select_weekly_highlights,
)
from aggregator.filters import TopicFilter
from aggregator.formatter import format_digest, format_post, format_weekly_digest
from aggregator.models import FeedItem
from aggregator.modes import RewriteMode, classify_mode
from aggregator.storage import Storage
from aggregator.telegram import TelegramClient
from aggregator.validator import FactPreservationValidator
from rewriter import FallbackRewriter, LLMRewriter, Rewriter

LOGGER = logging.getLogger("aggregator")


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def publication_day_window(
    timezone_name: str,
    now_utc: datetime | None = None,
) -> tuple[datetime, datetime]:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name != "Europe/Moscow":
            raise
        zone = timezone(timedelta(hours=3), name="Europe/Moscow")
    current = now_utc or datetime.now(timezone.utc)
    local_now = current.astimezone(zone)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


@dataclass(slots=True)
class RunStats:
    collected: int = 0
    filtered: int = 0
    duplicates: int = 0
    validation_failed: int = 0
    previews: int = 0
    sent: int = 0


@dataclass(slots=True)
class PreparedPost:
    item: FeedItem
    article_text: str
    body: str
    mode: RewriteMode
    topics: list[str]
    post: str
    updated: bool = False


def digest_title(timezone_name: str, now_utc: datetime | None = None) -> str:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name != "Europe/Moscow":
            raise
        zone = timezone(timedelta(hours=3), name="Europe/Moscow")
    local_hour = (now_utc or datetime.now(timezone.utc)).astimezone(zone).hour
    return "Утренний дайджест" if local_hour < 14 else "Вечерний дайджест"


def telegram_post_url(channel: str, message_id: object) -> str | None:
    username = channel.lstrip("@").strip()
    if not username or not isinstance(message_id, int):
        return None
    return f"https://t.me/{username}/{message_id}"


def load_sources(path: Path) -> list[dict[str, object]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("sources", []))


def choose_rewriter() -> Rewriter:
    if os.getenv("LLM_API_KEY"):
        LOGGER.info("Rewrite: LLM provider=%s model=%s", os.getenv("LLM_PROVIDER", "openai"), os.getenv("LLM_MODEL", "gpt-4.1-mini"))
        return LLMRewriter.from_env()
    LOGGER.info("LLM_API_KEY отсутствует: используется безопасный fallback")
    return FallbackRewriter()


def emit_preview(item_source: str, title: str, mode: str, topics: list[str], duplicate: bool, post: str) -> None:
    print(
        "\n".join(
            [
                "=" * 72,
                f"SOURCE: {item_source}",
                f"TITLE: {title}",
                f"MODE: {mode}",
                f"MATCHED TOPICS: {', '.join(topics)}",
                f"DUPLICATE: {str(duplicate).lower()}",
                "",
                "PREVIEW:",
                post,
            ]
        )
    )


def run(args: argparse.Namespace) -> RunStats:
    sources = load_sources(args.config)
    storage = Storage(args.state)
    collector = FeedCollector()
    article_fetcher = ArticleFetcher()
    topic_filter = TopicFilter()
    deduplicator = Deduplicator(storage)
    rewriter = choose_rewriter()
    validator = FactPreservationValidator()
    stats = RunStats()

    review_mode = env_bool("REVIEW_MODE", True)
    review_chat_id = os.getenv("REVIEW_CHAT_ID", "").strip()
    channel = os.getenv("TELEGRAM_CHANNEL", "@GVOZDIchKAAA").strip()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram = TelegramClient(token) if token else None
    daily_limit = max(0, int(os.getenv("DAILY_POST_LIMIT", "10")))
    post_timezone = os.getenv("POST_TIMEZONE", "Europe/Moscow").strip()
    digest_mode = args.digest or env_bool("DIGEST_MODE", False)
    published_today = 0
    effective_max_items = args.max_items
    prepared_posts: list[PreparedPost] = []

    try:
        if not args.dry_run and not review_mode and daily_limit:
            day_start, day_end = publication_day_window(post_timezone)
            published_today = storage.count_published_between(day_start, day_end)
            remaining = max(0, daily_limit - published_today)
            if remaining == 0:
                LOGGER.info(
                    "Дневной лимит достигнут: published=%d limit=%d timezone=%s",
                    published_today,
                    daily_limit,
                    post_timezone,
                )
                return stats
            effective_max_items = min(effective_max_items, remaining)
            LOGGER.info(
                "Лимит публикаций: published=%d remaining=%d limit=%d timezone=%s",
                published_today,
                remaining,
                daily_limit,
                post_timezone,
            )

        if args.weekly_digest:
            rows = storage.published_since(datetime.now(timezone.utc) - timedelta(days=7))
            if not rows:
                LOGGER.info("Для недельного дайджеста пока нет опубликованных материалов")
                return stats
            weekly_post = format_weekly_digest(select_weekly_highlights(rows), channel)
            print("\n".join(["=" * 72, "WEEKLY DIGEST PREVIEW:", weekly_post]))
            stats.previews = 1
            status = "DRY_RUN" if args.dry_run else "REVIEW_READY"
            message_id: int | None = None
            destination = review_chat_id if review_mode else channel
            should_send = not args.dry_run and telegram is not None and bool(destination)
            if should_send:
                try:
                    message_id = telegram.send(destination, weekly_post)
                    status = "SENT_TO_REVIEW" if review_mode else "PUBLISHED"
                    stats.sent = 1
                except Exception as exc:
                    status = "SEND_FAILED"
                    LOGGER.error("Telegram-отправка итогов недели не удалась: %s", type(exc).__name__)
            iso_year, iso_week, _ = datetime.now(timezone.utc).date().isocalendar()
            storage.save(
                FeedItem(
                    source="Редакция",
                    title=f"Итоги недели {iso_year}-{iso_week:02d}",
                    url=f"https://t.me/{channel.lstrip('@')}?weekly={iso_year}-{iso_week:02d}",
                ),
                status,
                weekly_post,
                RewriteMode.ANALYSIS.value,
                ["weekly_digest"],
                telegram_message_id=message_id,
            )
            return stats

        items = collector.collect_all(sources)
        items.sort(
            key=lambda item: item.published_at.timestamp() if item.published_at else 0,
            reverse=True,
        )
        if not digest_mode and not args.emergency_only:
            items = rotate_items(items, published_today)
        stats.collected = len(items)
        produced = 0
        emoji_cursor = published_today
        recent_publications = storage.recent_published()
        for item in items:
            topic_match = topic_filter.match(item)
            if not topic_match.accepted:
                storage.save(item, "FILTERED_OUT")
                stats.filtered += 1
                continue

            if args.emergency_only and not is_emergency(item, topic_match.topics):
                storage.save(item, "NOT_EMERGENCY")
                stats.filtered += 1
                continue

            is_duplicate, reason = deduplicator.find_duplicate(item.url, item.title)
            correction = is_correction(item.title, item.description)
            updated = bool(
                is_duplicate
                and reason
                and not reason.startswith("exact:")
                and is_meaningful_update(item.title, item.description)
            )
            exact_duplicate = bool(reason and reason.startswith("exact:"))
            changed_correction = False
            if exact_duplicate and correction:
                previous_version = storage.find_by_normalized_url(normalize_url(item.url))
                changed_correction = bool(
                    previous_version
                    and normalize_title(item.title) != str(previous_version["normalized_title"])
                )
            if is_duplicate and (
                (exact_duplicate and not changed_correction)
                or (not exact_duplicate and not updated and not correction)
            ):
                LOGGER.info("Дубль: %s (%s)", item.title, reason)
                stats.duplicates += 1
                continue

            if not updated and any(stories_match(item.title, prepared.item.title) for prepared in prepared_posts):
                LOGGER.info("Дубль внутри текущей подборки: %s", item.title)
                stats.duplicates += 1
                continue

            if digest_mode and any(item.source == prepared.item.source for prepared in prepared_posts):
                LOGGER.info("Для разнообразия дайджеста пропущен второй материал %s", item.source)
                continue

            article = article_fetcher.fetch(item)
            correction = correction or is_correction(item.title, article.text)
            if item.canonical_url and item.canonical_url != item.url:
                is_duplicate, reason = deduplicator.find_duplicate(item.canonical_url, item.title)
                exact_duplicate = bool(reason and reason.startswith("exact:"))
                changed_correction = False
                if exact_duplicate and correction:
                    previous_version = storage.find_by_normalized_url(normalize_url(item.canonical_url))
                    changed_correction = bool(
                        previous_version
                        and normalize_title(item.title) != str(previous_version["normalized_title"])
                    )
                if is_duplicate and (
                    (exact_duplicate and not changed_correction)
                    or (not exact_duplicate and not updated and not correction)
                ):
                    LOGGER.info("Дубль canonical URL: %s (%s)", item.title, reason)
                    stats.duplicates += 1
                    continue

            mode = classify_mode(item.title, article.text)
            related = related_publication(item.title, recent_publications)
            promise_result = None
            if mode != RewriteMode.HARD_NEWS:
                promise_result = promise_result_publication(item.title, article.text, recent_publications)
            if correction and mode != RewriteMode.HARD_NEWS:
                mode = RewriteMode.ANALYSIS
            try:
                body = rewriter.rewrite(article, mode)
            except Exception as exc:  # provider failure must not stop the run
                LOGGER.warning("LLM rewrite не удался (%s), используется fallback", type(exc).__name__)
                body = FallbackRewriter().rewrite(article, mode)

            validation_source = f"{item.title}\n{article.text}\n{item.description}"
            validation = validator.validate(validation_source, body, title=item.title)
            if not validation.valid:
                error = "; ".join(validation.errors)
                storage.save(item, "VALIDATION_FAILED", article.text, mode.value, topic_match.topics, error)
                LOGGER.error("VALIDATION_FAILED: %s — %s", item.title, error)
                stats.validation_failed += 1
                continue

            corroborated_by = corroborating_sources(item, items)
            confidence = confirmation_level(validation_source, corroborated_by)
            previous = promise_result or related
            previous_url = telegram_post_url(channel, previous["telegram_message_id"]) if previous else None
            quote = None
            if mode != RewriteMode.HARD_NEWS and published_today % 4 == 2:
                quote = extract_title_quote(item.title)
            post = format_post(
                body,
                item.source,
                item.canonical_url or item.url,
                emoji_index=emoji_cursor,
                topics=topic_match.topics,
                mode=mode,
                corroborated_by=corroborated_by,
                updated=updated,
                confirmation=confidence.value,
                previous_title=str(previous["title"]) if previous and previous_url else None,
                previous_url=previous_url,
                correction=correction,
                promise_result=bool(promise_result),
                quote=quote,
            )
            emoji_cursor += 1
            emit_preview(item.source, item.title, mode.value, topic_match.topics, False, post)
            stats.previews += 1
            produced += 1

            if digest_mode:
                prepared_posts.append(
                    PreparedPost(
                        item=item,
                        article_text=article.text,
                        body=body,
                        mode=mode,
                        topics=topic_match.topics,
                        post=post,
                        updated=updated,
                    )
                )
                if produced >= effective_max_items:
                    break
                continue

            status = "DRY_RUN" if args.dry_run else "REVIEW_READY"
            message_id: int | None = None
            destination = review_chat_id if review_mode else channel
            should_send = not args.dry_run and telegram is not None and bool(destination)
            if should_send:
                try:
                    message_id = telegram.send(destination, post)
                    status = "SENT_TO_REVIEW" if review_mode else "PUBLISHED"
                    stats.sent += 1
                    if status == "PUBLISHED":
                        published_today += 1
                except Exception as exc:
                    status = "SEND_FAILED"
                    LOGGER.error("Telegram-отправка не удалась: %s", type(exc).__name__)
            elif not args.dry_run:
                if review_mode:
                    LOGGER.info("REVIEW_CHAT_ID или токен не задан: preview оставлен в log")
                else:
                    status = "CONFIGURATION_ERROR"
                    LOGGER.error("Autopublish запрошен, но Telegram token/channel не настроены")

            storage.save(
                item,
                status,
                article.text,
                mode.value,
                topic_match.topics,
                telegram_message_id=message_id,
            )
            if produced >= effective_max_items:
                break

        if digest_mode and prepared_posts:
            digest_entries = [
                (
                    ("ОБНОВЛЕНО — " if prepared.updated else "") + prepared.body,
                    prepared.item.source,
                    prepared.item.canonical_url or prepared.item.url,
                )
                for prepared in prepared_posts
            ]
            digest = format_digest(digest_entries, digest_title(post_timezone))
            print("\n".join(["=" * 72, "DIGEST PREVIEW:", digest]))
            status = "DRY_RUN" if args.dry_run else "REVIEW_READY"
            message_id: int | None = None
            destination = review_chat_id if review_mode else channel
            should_send = not args.dry_run and telegram is not None and bool(destination)
            if should_send:
                try:
                    message_id = telegram.send(destination, digest)
                    status = "SENT_TO_REVIEW" if review_mode else "PUBLISHED"
                    stats.sent += 1
                except Exception as exc:
                    status = "SEND_FAILED"
                    LOGGER.error("Telegram-отправка дайджеста не удалась: %s", type(exc).__name__)
            elif not args.dry_run:
                status = "REVIEW_READY" if review_mode else "CONFIGURATION_ERROR"

            for prepared in prepared_posts:
                storage.save(
                    prepared.item,
                    status,
                    prepared.article_text,
                    prepared.mode.value,
                    prepared.topics,
                    telegram_message_id=message_id,
                )
    finally:
        storage.close()
    return stats


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Russian-language Telegram news aggregator")
    parser.add_argument("--config", type=Path, default=Path("config/sources.yaml"))
    parser.add_argument("--state", type=Path, default=Path(os.getenv("STATE_DB", "data/state.db")))
    parser.add_argument("--max-items", type=int, default=int(os.getenv("MAX_ITEMS_PER_RUN", "5")))
    parser.add_argument("--dry-run", action="store_true", help="Never contact Telegram; print previews")
    parser.add_argument("--digest", action="store_true", help="Combine selected stories into one digest")
    parser.add_argument("--emergency-only", action="store_true", help="Publish only fresh urgent stories")
    parser.add_argument("--weekly-digest", action="store_true", help="Summarize the previous seven days")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    stats = run(args)
    LOGGER.info(
        "Итог: collected=%d filtered=%d duplicates=%d validation_failed=%d previews=%d sent=%d",
        stats.collected,
        stats.filtered,
        stats.duplicates,
        stats.validation_failed,
        stats.previews,
        stats.sent,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
