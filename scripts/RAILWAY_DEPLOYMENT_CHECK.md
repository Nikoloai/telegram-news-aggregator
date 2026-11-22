# Railway deployment runbook for Гвоздика

Normal production configuration for the existing `news-publisher` service:

- Start command: `python -m scripts.railway_cron --live`
- Cron schedule: `*/15 * * * *` (UTC); rapid checks retain the daily cap and existing release rules.
- Predeploy: `python -m pytest -q`
- State: persistent SQLite volume `/data/state.db`
- Channel: `@GVOZDIchKAAA`

**Do not interpret a Railway deploy SUCCESS as proof of a news publication.** Check the runtime logs after the next scheduled invocation for `Persistent state verified`, `Telegram bot=...`, successful RSS fetches, and `Scheduled-release run complete`. Then inspect the `Итог` counters. `sent=0` may correctly mean no eligible daytime slot and is not proof of LLM/content success.

**Important:** A Railway redeploy of an existing build can retain that build's old start command despite later configuration changes. After a one-off script or temporary dry-run, restore the normal start command *and trigger a fresh build from a new GitHub commit*. Confirm the actual executed command from logs, not only from the configuration panel.

For a non-publishing integration smoke test use a disposable state copy:

```sh
cp /data/state.db /tmp/gvozdika-smoke.db
python main.py --dry-run --max-items 1 --max-candidates 12 --state /tmp/gvozdika-smoke.db
```

A legitimate smoke result must include the `Итог` counters and, ideally, `previews=1`. Do not treat predeploy unit-test output as this integration test. Never aim a smoke run at the real production SQLite file. Use Telegram channel sends only for specifically approved posts and keep deduplication keys.

For the 2026-11-19 incident, the one-off publisher used for post #1002 inadvertently remained the executed command after a config-only redeploy. This caused ordinary cron invocations to skip news. Restoring config alone is insufficient; always verify a newly built container.

## Verified integration smoke test, 2026-11-19

At 23:05 UTC, a fresh-GitHub-build cron run used a disposable SQLite state copy, exercised actual RSS sources and OpenAI generation, and returned `collected=177`, `previews=2`, `validation_failed=1`, `sent=0` (expected for dry run). This was a meaningful integration smoke, **not** an end-to-end live Telegram send. The full live publisher must be restored with a **new commit/build** and checked again; a plain config redeploy may execute the previous command.
