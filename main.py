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
from aggregator.dedupe import Deduplicator
from aggregator.filters import TopicFilter
from aggregator.formatter import format_post
from aggregator.modes import classify_mode
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
    published_today = 0
    effective_max_items = args.max_items

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

        items = collector.collect_all(sources)
        items.sort(
            key=lambda item: item.published_at.timestamp() if item.published_at else 0,
            reverse=True,
        )
        stats.collected = len(items)
        produced = 0
        for item in items:
            topic_match = topic_filter.match(item)
            if not topic_match.accepted:
                storage.save(item, "FILTERED_OUT")
                stats.filtered += 1
                continue

            is_duplicate, reason = deduplicator.find_duplicate(item.url, item.title)
            if is_duplicate:
                LOGGER.info("Дубль: %s (%s)", item.title, reason)
                stats.duplicates += 1
                continue

            article = article_fetcher.fetch(item)
            if item.canonical_url and item.canonical_url != item.url:
                is_duplicate, reason = deduplicator.find_duplicate(item.canonical_url, item.title)
                if is_duplicate:
                    LOGGER.info("Дубль canonical URL: %s (%s)", item.title, reason)
                    stats.duplicates += 1
                    continue

            mode = classify_mode(item.title, article.text)
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

            post = format_post(body, item.source, item.canonical_url or item.url)
            emit_preview(item.source, item.title, mode.value, topic_match.topics, False, post)
            stats.previews += 1
            produced += 1

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
    finally:
        storage.close()
    return stats


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Russian-language Telegram news aggregator")
    parser.add_argument("--config", type=Path, default=Path("config/sources.yaml"))
    parser.add_argument("--state", type=Path, default=Path(os.getenv("STATE_DB", "data/state.db")))
    parser.add_argument("--max-items", type=int, default=int(os.getenv("MAX_ITEMS_PER_RUN", "5")))
    parser.add_argument("--dry-run", action="store_true", help="Never contact Telegram; print previews")
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
