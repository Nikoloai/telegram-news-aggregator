from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from dotenv import load_dotenv

from aggregator.article import ArticleFetcher, clean_html
from aggregator.collector import FeedCollector
from aggregator.corroboration import source_evidence, stories_match
from aggregator.dedupe import Deduplicator, is_meaningful_update, normalize_title, normalize_url
from aggregator.editorial import (
    confirmation_level,
    extract_title_quote,
    is_correction,
    is_emergency,
    promise_result_publication,
    publication_context,
    rank_news,
    related_publication,
    review_reasons,
    select_weekly_highlights,
)
from aggregator.filters import TopicFilter
from aggregator.formatter import format_digest, format_post, format_weekly_digest
from aggregator.models import FeedItem
from aggregator.modes import RewriteMode, classify_mode
from aggregator.review import notify_pending, process_editor_messages
from aggregator.schedule import due_posts, publication_slots, weekly_due
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


def publication_zone(timezone_name: str) -> ZoneInfo | timezone:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name != "Europe/Moscow":
            raise
        return timezone(timedelta(hours=3), name="Europe/Moscow")


def publication_day_window(
    timezone_name: str,
    now_utc: datetime | None = None,
) -> tuple[datetime, datetime]:
    zone = publication_zone(timezone_name)
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
    shortfall: int = 0


@dataclass(slots=True)
class PreparedPost:
    item: FeedItem
    article_text: str
    body: str
    mode: RewriteMode
    topics: list[str]
    post: str
    updated: bool = False
    rubric: str | None = None


def digest_title(timezone_name: str, now_utc: datetime | None = None) -> str:
    zone = publication_zone(timezone_name)
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
    args = argparse.Namespace(**vars(args))
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
    editor_chat_id = os.getenv("EDITOR_CHAT_ID", "").strip()
    channel = os.getenv("TELEGRAM_CHANNEL", "@GVOZDIchKAAA").strip()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram = TelegramClient(token) if token else None
    daily_limit = max(0, int(os.getenv("DAILY_POST_LIMIT", "10")))
    post_timezone = os.getenv("POST_TIMEZONE", "Europe/Moscow").strip()
    digest_mode = args.digest or env_bool("DIGEST_MODE", False)
    published_today = 0
    effective_max_items = args.max_items
    expected_posts = 0
    published_before_release = 0
    prepared_posts: list[PreparedPost] = []

    try:
        day_start, day_end = publication_day_window(post_timezone)
        local_now = datetime.now(timezone.utc).astimezone(publication_zone(post_timezone))
        iso_year, iso_week, _ = local_now.date().isocalendar()
        weekly_item = FeedItem(
            source="Редакция", title=f"Итоги недели {iso_year}-{iso_week:02d}",
            url=f"https://t.me/{channel.lstrip('@')}?weekly={iso_year}-{iso_week:02d}",
        )
        quote_used = storage.rubric_count_between("quote", day_start, day_end) > 0
        if not args.dry_run and telegram:
            try:
                stats.sent += process_editor_messages(
                    storage, telegram, editor_chat_id, channel, day_start, day_end,
                    daily_limit, allow_publish=not review_mode,
                )
                notify_pending(storage, telegram, editor_chat_id)
            except Exception as exc:
                LOGGER.warning("Обработка личных команд не удалась: %s", type(exc).__name__)
            quote_used = storage.rubric_count_between("quote", day_start, day_end) > 0
        if args.process_editor_messages:
            return stats
        if args.scheduled_release:
            LOGGER.info("Плановые времена по Москве: %s", ", ".join(
                f"{hour:02d}:{minute:02d}" for hour, minute in publication_slots(local_now.date())
            ))
            # A delayed invocation catches up to the cumulative plan, not just one post.
            published_today = storage.count_published_between(day_start, day_end)
            published_before_release = published_today
            has_weekly = bool(storage.find_exact(normalize_url(weekly_item.url), normalize_title(weekly_item.title)))
            if weekly_due(local_now) and not has_weekly:
                args.weekly_digest = True
            else:
                target = due_posts(local_now, daily_limit)
                if local_now.weekday() == 6 and not has_weekly:
                    target = min(target, max(0, daily_limit - 1))
                expected_posts = max(0, target - published_today)
                effective_max_items = expected_posts
                digest_mode = False
                if not expected_posts:
                    args.emergency_only = True
                    effective_max_items = 1
                LOGGER.info("Суточный план: published=%d due=%d needed=%d limit=%d timezone=%s",
                            published_today, target if not args.weekly_digest else daily_limit,
                            expected_posts, daily_limit, post_timezone)
        if args.quote_of_day and quote_used:
            LOGGER.info("Цитата дня уже опубликована")
            return stats
        if not args.dry_run and not review_mode and daily_limit:
            day_start, day_end = publication_day_window(post_timezone)
            published_today = storage.count_published_between(day_start, day_end)
            remaining = max(0, daily_limit - published_today)
            if local_now.weekday() == 6 and not args.weekly_digest and not args.emergency_only:
                if not storage.find_exact(normalize_url(weekly_item.url), normalize_title(weekly_item.title)):
                    remaining = max(0, remaining - 1)
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
            if storage.find_exact(normalize_url(weekly_item.url), normalize_title(weekly_item.title)):
                LOGGER.info("Итоги этой недели уже подготовлены или опубликованы")
                return stats
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
            storage.save(
                weekly_item,
                status,
                weekly_post,
                RewriteMode.ANALYSIS.value,
                ["weekly_digest"],
                telegram_message_id=message_id,
                post_html=weekly_post,
                rubric="weekly",
            )
            return stats

        items = collector.collect_all(sources)
        items.sort(
            key=lambda item: item.published_at.timestamp() if item.published_at else 0,
            reverse=True,
        )
        stats.collected = len(items)
        produced = 0
        held_for_review = 0
        emoji_cursor = published_today
        recent_publications = storage.recent_published()
        items = rank_news(items, recent_publications, published_today,
                          max_age_hours=max(3, int(os.getenv("NEWS_MAX_AGE_HOURS", "48"))))
        candidates_checked = 0
        for item in items:
            if args.quote_of_day and not extract_title_quote(item.title):
                continue
            topic_match = topic_filter.match(item)
            if not topic_match.accepted:
                storage.save(item, "FILTERED_OUT")
                stats.filtered += 1
                continue

            emergency = is_emergency(item, topic_match.topics)
            if args.emergency_only and not emergency:
                storage.save(item, "NOT_EMERGENCY")
                stats.filtered += 1
                continue

            is_duplicate, reason = deduplicator.find_duplicate(item.url, item.title)
            correction = is_correction(item.title, item.description)
            prior_promise = promise_result_publication(item.title, clean_html(item.description), recent_publications)
            promise_update = bool(prior_promise and reason and not reason.startswith("exact:")
                                  and reason.endswith(":" + str(prior_promise["url"])))
            feed_signature = hashlib.sha256(f"{item.title}\n{clean_html(item.description)}".encode()).hexdigest()
            correction_key = f"correction_seen:{normalize_url(item.canonical_url or item.url)}"
            updated = bool(
                is_duplicate
                and reason
                and not reason.startswith("exact:")
                and (is_meaningful_update(item.title, item.description) or promise_update)
            )
            exact_duplicate = bool(reason and reason.startswith("exact:"))
            changed_correction = False
            if exact_duplicate and correction:
                previous_version = storage.find_by_normalized_url(normalize_url(item.url))
                changed_correction = bool(
                    previous_version
                    and (normalize_title(item.title) != str(previous_version["normalized_title"])
                         or storage.get_meta(correction_key) != feed_signature)
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

            candidates_checked += 1
            if candidates_checked > max(1, args.max_candidates):
                LOGGER.warning("Достигнут лимит проверки кандидатов: %d", args.max_candidates)
                break
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
                        and (normalize_title(item.title) != str(previous_version["normalized_title"])
                             or storage.get_meta(f"correction_seen:{normalize_url(item.canonical_url)}") != feed_signature)
                    )
                if is_duplicate and (
                    (exact_duplicate and not changed_correction)
                    or (not exact_duplicate and not updated and not correction)
                ):
                    LOGGER.info("Дубль canonical URL: %s (%s)", item.title, reason)
                    stats.duplicates += 1
                    continue

            mode = classify_mode(item.title, article.text)
            if args.emergency_only:
                mode = RewriteMode.HARD_NEWS
            related = related_publication(item.title, recent_publications)
            promise_result = None
            if mode != RewriteMode.HARD_NEWS:
                promise_result = promise_result_publication(item.title, article.text, recent_publications)
            if correction and mode != RewriteMode.HARD_NEWS:
                mode = RewriteMode.ANALYSIS
            quote = None
            if not quote_used and mode != RewriteMode.HARD_NEWS and not correction and not promise_result:
                quote = extract_title_quote(item.title)
            if args.quote_of_day and not quote:
                continue
            try:
                body = rewriter.rewrite(article, mode)
            except Exception as exc:  # provider failure must not stop the run
                LOGGER.warning("LLM rewrite не удался (%s), используется fallback", type(exc).__name__)
                body = FallbackRewriter().rewrite(article, mode)

            validation_source = f"{item.title}\n{article.text}\n{item.description}"
            validation = validator.validate(validation_source, body, title=item.title)
            rewrite_warnings: list[str] = []
            if not validation.valid:
                error = "; ".join(validation.errors)
                safe_body = FallbackRewriter().rewrite(article, mode)
                safe_validation = validator.validate(validation_source, safe_body, title=item.title)
                if not safe_validation.valid:
                    storage.save(item, "VALIDATION_FAILED", article.text, mode.value, topic_match.topics, error)
                    LOGGER.error("VALIDATION_FAILED: %s — %s", item.title, error)
                    stats.validation_failed += 1
                    continue
                body, validation = safe_body, safe_validation
                rewrite_warnings.append("Переписывание изменило факты; подготовлен исходный пересказ для проверки")

            evidence = source_evidence(item, items)
            corroborated_by = evidence.sources
            focal_source = f"{item.title}\n{clean_html(item.description)[:600]}"
            confidence = confirmation_level(focal_source, corroborated_by, evidence.shared_origins, evidence.conflicts)
            previous = promise_result or related
            previous_url = telegram_post_url(channel, previous["telegram_message_id"]) if previous else None
            context = publication_context(previous, promise=bool(promise_result))
            urgent_post = args.emergency_only or (args.scheduled_release and emergency)
            rubric = "correction" if correction else "promise_result" if promise_result else "quote" if quote else "emergency" if urgent_post else None
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
                context=context,
                shared_origins=evidence.shared_origins,
                emergency=urgent_post,
            )
            emoji_cursor += 1
            emit_preview(item.source, item.title, mode.value, topic_match.topics, False, post)
            stats.previews += 1
            reasons = review_reasons(validation_source, body, mode, evidence.conflicts, validation.warnings)
            reasons.extend(rewrite_warnings)
            if reasons and env_bool("REVIEW_UNCERTAIN", True):
                LOGGER.info("Материал удержан для редактора: %s", "; ".join(reasons))
                if not args.dry_run and held_for_review < 3:
                    storage.enqueue_review(item, post, validation_source, mode.value, topic_match.topics,
                                           reasons, rubric=rubric, body_text=body)
                    storage.save(item, "REVIEW_PENDING", article.text, mode.value, topic_match.topics,
                                 post_html=post, rubric=rubric)
                    if correction:
                        storage.set_meta(f"correction_seen:{normalize_url(item.canonical_url or item.url)}", feed_signature)
                    if telegram:
                        notify_pending(storage, telegram, editor_chat_id)
                held_for_review += 1
                # Three disputed stories must not block all later safe stories.
                continue
            if quote:
                quote_used = True
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
                        rubric=rubric,
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
                    message_id = telegram.send(destination, post, preview_url=item.canonical_url or item.url)
                    status = "SENT_TO_REVIEW" if review_mode else "PUBLISHED"
                    stats.sent += 1
                    if status == "PUBLISHED":
                        published_today += 1
                except Exception as exc:
                    status = "SEND_FAILED"
                    produced -= 1
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
                post_html=post,
                rubric=rubric,
            )
            if correction and status in {"PUBLISHED", "SENT_TO_REVIEW", "REVIEW_READY", "DRY_RUN"}:
                storage.set_meta(f"correction_seen:{normalize_url(item.canonical_url or item.url)}", feed_signature)
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
            digest = format_digest(digest_entries, digest_title(post_timezone),
                                   formatted_posts=[prepared.post for prepared in prepared_posts])
            print("\n".join(["=" * 72, "DIGEST PREVIEW:", digest]))
            status = "DRY_RUN" if args.dry_run else "REVIEW_READY"
            message_id: int | None = None
            destination = review_chat_id if review_mode else channel
            should_send = not args.dry_run and telegram is not None and bool(destination)
            if should_send:
                try:
                    first_item = prepared_posts[0].item
                    message_id = telegram.send(destination, digest, preview_url=first_item.canonical_url or first_item.url)
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
                    post_html=prepared.post,
                    rubric=prepared.rubric,
                )
                if prepared.rubric == "correction" and status in {"PUBLISHED", "SENT_TO_REVIEW", "REVIEW_READY", "DRY_RUN"}:
                    signature = hashlib.sha256(f"{prepared.item.title}\n{clean_html(prepared.item.description)}".encode()).hexdigest()
                    storage.set_meta(f"correction_seen:{normalize_url(prepared.item.canonical_url or prepared.item.url)}", signature)
        if expected_posts:
            achieved = produced if args.dry_run else max(
                0, storage.count_published_between(day_start, day_end) - published_before_release
            )
            stats.shortfall = max(0, expected_posts - achieved)
            if stats.shortfall:
                LOGGER.warning("Недобор суточного плана: не хватает %d постов; пригодные новости исчерпаны или отправка не удалась",
                               stats.shortfall)
                if not args.dry_run and telegram and editor_chat_id.isdecimal() and int(editor_chat_id) > 0:
                    key = "shortfall_notice:" + day_start.isoformat()
                    if storage.get_meta(key) != str(stats.shortfall):
                        try:
                            telegram.send(editor_chat_id, f"⚠️ Недобор плана: не хватает {stats.shortfall} постов к текущему времени. "
                                          "Спорные материалы и дубли не опубликованы ради количества.")
                            storage.set_meta(key, str(stats.shortfall))
                        except Exception as exc:
                            LOGGER.warning("Не удалось сообщить о недоборе: %s", type(exc).__name__)
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
    parser.add_argument("--quote-of-day", action="store_true", help="Select an attributed quote, at most once a day")
    parser.add_argument("--process-editor-messages", action="store_true", help="Handle private editor commands only")
    parser.add_argument("--scheduled-release", action="store_true", help="Catch up to the ten-post daily plan")
    parser.add_argument("--max-candidates", type=int, default=40, help="Limit article extraction and rewriting per run")
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
        "Итог: collected=%d filtered=%d duplicates=%d validation_failed=%d previews=%d sent=%d shortfall=%d",
        stats.collected,
        stats.filtered,
        stats.duplicates,
        stats.validation_failed,
        stats.previews,
        stats.sent,
        stats.shortfall,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
