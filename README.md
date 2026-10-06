# Telegram News Aggregator

Безопасный MVP русскоязычного новостного агрегатора для Telegram-канала
[`@GVOZDIchKAAA`](https://t.me/GVOZDIchKAAA). Он получает материалы из RSS, отбирает
темы канала, удаляет дубли, извлекает текст статьи, определяет редакционный режим,
создаёт оригинальный короткий пост, проверяет сохранность фактов и отправляет результат
на review. Публикация в публичный канал по умолчанию **выключена**.

Это не RSS-репостер и не fact-checker. Исходная публикация используется как источник
фактов, длинные фрагменты не копируются, а атрибуция и степень уверенности должны
сохраняться.

## Pipeline

```text
RSS → topic filter → URL/title dedupe → article extraction → mode classifier
    → LLM or fallback rewrite → fact-preservation validator → review/publish → SQLite
```

1. `FeedCollector` читает только явно настроенные RSS/Atom feeds.
2. `TopicFilter` анализирует заголовок, описание и RSS-категории. Случайные зарубежные,
   спортивные, lifestyle- и развлекательные материалы отсекаются правилами.
3. `Deduplicator` сравнивает canonical/normalized URL, нормализованный заголовок,
   token-set similarity и эвристику одинакового именованного события.
4. `ArticleFetcher` берёт canonical URL и абзацы из `article`/`main`; при недоступности
   страницы использует RSS description или метаописание.
5. `classify_mode` выбирает `HARD_NEWS`, `ANALYSIS` или `IRONIC`. Сигналы о жертвах,
   войне, насилии, арестах и катастрофах всегда имеют приоритет и запрещают иронию.
6. Rewriter создаёт Telegram-текст. Профиль — короткий сильный лид, компактные абзацы,
   плотная фактическая подача в ритме современных новостных каналов; чужие формулировки
   и уникальный голос не копируются. `⚡` допускается редко, не механически.
7. Validator отклоняет новые/изменённые числа, даты, суммы, проценты, имена и организации.
   Это защита от ошибок переписывания, а не проверка истинности исходника.
8. В `REVIEW_MODE=true` пост идёт в review-чат либо только в log. Только явное
   `REVIEW_MODE=false` разрешает отправку в `TELEGRAM_CHANNEL`.

Архитектура разделяет сбор, фильтрацию, дедупликацию, rewrite и validation. Позже можно
добавить fact checking, provenance, поиск первоисточника, проверку медиа и сопоставление
нескольких источников без переписывания transport/storage слоя.

## Подключённые источники

Конфигурация находится в [`config/sources.yaml`](config/sources.yaml).

| Источник | Feed | Статус проверки |
|---|---|---|
| Mediazona | `https://zona.media/rss` | официальный RSS, работает |
| ОВД-Инфо | `https://ovd.info/rss.xml` | официальный RSS, работает |
| Настоящее Время | `https://www.currenttime.tv/api/zgbip_l-vomx-tpe-v_py` | официальный news RSS, работает |
| Meduza | `https://meduza.io/rss/all` | русский RSS, работает |
| The Insider | `https://theins.ru/feed` | официальный RSS, работает |

Проверка выполнена реальным dry-run 6 октября 2026 года. Scraping списков новостей не
используется. Извлечение текста со страницы — best effort; RSS остаётся надёжным fallback.

Чтобы добавить источник, добавьте элемент:

```yaml
- name: Название
  feed_url: https://example.org/rss.xml
  homepage: https://example.org/
  enabled: true
  max_items: 30
```

Сначала проверьте, что это стабильный официальный feed, а не HTML-страница или временный
URL.

## Установка и локальный запуск

Нужен Python 3.11+ (workflow использует 3.12).

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
python main.py --dry-run
```

Dry-run никогда не вызывает Telegram. По умолчанию он печатает не более пяти previews;
лимит меняется через `--max-items 10` или `MAX_ITEMS_PER_RUN`.

Тесты:

```bash
python -m pytest
```

## Rewrite: LLM и fallback

Abstraction находится в `rewriter/base.py`, реализации — в `rewriter/llm.py` и
`rewriter/fallback.py`.

Если `LLM_API_KEY` задан, используется OpenAI-compatible Chat Completions endpoint:

```dotenv
LLM_PROVIDER=openai
LLM_API_KEY=...
LLM_MODEL=gpt-4.1-mini
# Для совместимого провайдера:
# LLM_PROVIDER=openai_compatible
# LLM_BASE_URL=https://provider.example/v1
```

Если ключа нет или провайдер временно отвечает ошибкой, запуск не падает: fallback
очищает RSS description, сокращает его и оформляет короткий пост с источником. Fallback
не придумывает аналитический контекст.

## Validation и статусы

Материал может получить, среди прочего, статусы:

- `FILTERED_OUT` — не прошёл тему;
- `VALIDATION_FAILED` — rewrite добавил/изменил проверяемый факт;
- `DRY_RUN` — preview создан локально;
- `REVIEW_READY` — preview выведен в log, review-чат не настроен;
- `SENT_TO_REVIEW` — отправлен редактору;
- `PUBLISHED` — опубликован при явно выключенном review mode;
- `SEND_FAILED` / `CONFIGURATION_ERROR` — Telegram или конфигурация требуют внимания.

`VALIDATION_FAILED` никогда не публикуется. В SQLite сохраняются URL, canonical и
normalized URL, заголовок, источник, даты публикации/обнаружения, статус, hash текста,
режим, темы и Telegram `message_id`.

## Telegram setup

1. Создайте бота через `@BotFather`.
2. Для review добавьте бота в приватную группу/чат и узнайте числовой chat ID (обычно
   начинается с `-100` для супергруппы).
3. Для будущей публикации добавьте бота администратором канала с правом публикации.
4. Никогда не коммитьте токен или `.env`.

Локальные переменные:

```dotenv
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHANNEL=@GVOZDIchKAAA
REVIEW_CHAT_ID=-1001234567890
REVIEW_MODE=true
```

При `REVIEW_MODE=true`:

- с `REVIEW_CHAT_ID` preview отправляется только туда;
- без `REVIEW_CHAT_ID` preview остаётся только в log;
- публичный канал не используется.

## GitHub Secrets, Variables и Actions

В **Settings → Secrets and variables → Actions** добавьте secrets:

- `TELEGRAM_BOT_TOKEN` — токен Bot API;
- `REVIEW_CHAT_ID` — приватный review-чат;
- `LLM_API_KEY` — необязательно, fallback работает без него.

Repository variables:

- `REVIEW_MODE=true` — обязательный безопасный старт;
- `TELEGRAM_CHANNEL=@GVOZDIchKAAA`;
- `LLM_PROVIDER=openai` — необязательно;
- `LLM_MODEL=gpt-4.1-mini` — необязательно;
- `LLM_BASE_URL` — только для совместимого endpoint.

Workflow `.github/workflows/aggregator.yml` запускается каждый час на 17-й минуте и
вручную. `concurrency` не допускает параллельной публикации. Каждый запуск сначала
выполняет unit tests.

Для ручного запуска откройте **Actions → News aggregator → Run workflow**. Опция
`dry_run` по умолчанию включена; такой запуск не обращается к Telegram и не меняет
постоянное состояние.

### Persistence

Runner GitHub Actions эфемерен, поэтому cache не используется как источник истины.
После успешного не-dry запуска workflow сохраняет `data/state.db` единственным commit в
отдельную ветку `state`; перед следующим запуском база восстанавливается оттуда.
Служебная ветка перезаписывается только этим workflow. Для этого job имеет минимально
необходимое разрешение `contents: write`. В основной ветке `*.db` игнорируются.

Если организация запрещает запись `GITHUB_TOKEN`, включите **Settings → Actions →
General → Workflow permissions → Read and write permissions**.

## Переход к autopublish

Не меняйте код. После проверки previews:

1. убедитесь, что бот — администратор `@GVOZDIchKAAA`;
2. проверьте `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHANNEL`;
3. оставьте review включённым ещё на несколько плановых запусков;
4. измените repository variable `REVIEW_MODE` с `true` на `false`;
5. вручную запустите workflow с `dry_run=false` и проверьте одну публикацию;
6. чтобы немедленно вернуться в review, снова поставьте `REVIEW_MODE=true`.

## Security

- `.env`, базы, журналы SQLite, venv и IDE-файлы исключены из Git.
- Токен не печатается в logs; ошибки Telegram логируются только по типу.
- Автопубликация требует явного переключателя.
- Код не читает приватные Telegram-аккаунты и не делает массовых рассылок.
- Fact checking, OSINT, image/video verification, face recognition, tracking и political
  ad targeting намеренно не реализованы.

## Ограничения MVP

- Rule-based topic/mode classification требует периодической настройки словарей.
- Fuzzy dedupe распознаёт очевидные совпадения и однотипные именованные события, но не
  заменяет semantic clustering.
- Validator проверяет сохранность формальных сущностей, а не истинность заявления.
- Fallback обычно ближе к сжатому пересказу RSS; полноценный оригинальный редакционный
  текст и контекст лучше получать через LLM с обязательной post-validation.

