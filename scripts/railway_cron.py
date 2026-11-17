"""Railway two-hour cron entrypoint with fail-closed GitHub state migration.

Never publish against an unseeded or corrupted database. GitHub Actions formerly
persisted SQLite at branch "state"; Railway owns the migrated copy on /data.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

LOG = logging.getLogger("railway_cron")
STATE_SOURCE = (
    "https://raw.githubusercontent.com/"
    "Nikoloai/telegram-news-aggregator/state/data/state.db"
)
MAX_STATE_BYTES = 50 * 1024 * 1024


def check_state(path: Path) -> tuple[int, int]:
    """Require a real, healthy publication history before enabling the bot."""
    if not path.is_file() or path.stat().st_size < 100:
        raise RuntimeError("State is missing or unexpectedly small")
    if path.open("rb").read(16) != b"SQLite format 3\x00":
        raise RuntimeError("State is not SQLite")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("State failed SQLite integrity check")
        publications = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE status = 'PUBLISHED'"
        ).fetchone()[0]
        articles = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        if publications < 1:
            raise RuntimeError("No published history found; refusing fresh start")
        return articles, publications
    finally:
        conn.close()


def ensure_state(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        articles, publications = check_state(path)
        LOG.info("Persistent state verified: articles=%d published=%d", articles, publications)
        return

    LOG.info("Bootstrapping state from the existing GitHub state branch")
    fd, temporary = tempfile.mkstemp(prefix=".news-state-", suffix=".db", dir=path.parent)
    os.close(fd)
    temp_path = Path(temporary)
    try:
        with requests.get(STATE_SOURCE, stream=True, timeout=(10, 45)) as response:
            response.raise_for_status()
            total = 0
            with temp_path.open("wb") as target:
                for chunk in response.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_STATE_BYTES:
                        raise RuntimeError("Remote state exceeds size cap")
                    target.write(chunk)
        articles, publications = check_state(temp_path)
        os.replace(temp_path, path)
        LOG.info("State migrated: articles=%d published=%d", articles, publications)
    finally:
        temp_path.unlink(missing_ok=True)


def run_command(*argv: str) -> None:
    result = subprocess.run([sys.executable, *argv], check=False)
    if result.returncode:
        raise RuntimeError("One news pipeline command failed (exit %d)" % result.returncode)


def last_publication(path: Path) -> str | None:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = conn.execute("SELECT MAX(published_at) FROM publications").fetchone()
        return result[0] if result else None
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Enable actual publication")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    path = Path(os.environ.get("STATE_DB", "/data/state.db"))
    if path.resolve() != Path("/data/state.db"):
        LOG.error("Unexpected STATE_DB path; refusing publication")
        return 1
    try:
        ensure_state(path)
        if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("LLM_API_KEY"):
            raise RuntimeError("Publisher secrets are not configured")
        if os.environ.get("TELEGRAM_CHANNEL") != "@GVOZDIchKAAA":
            raise RuntimeError("Unexpected channel; refusing publication")
        if os.environ.get("REVIEW_MODE", "").lower() != "false":
            raise RuntimeError("REVIEW_MODE must explicitly be false for this deployment")
        run_command("-m", "scripts.check_telegram_access")
        if not args.live:
            with tempfile.TemporaryDirectory(prefix="railway-dry-run-") as temp_dir:
                dry_state = Path(temp_dir) / "state.db"
                shutil.copy2(path, dry_state)
                run_command("main.py", "--dry-run", "--scheduled-release",
                            "--max-items", "1", "--state", str(dry_state))
            LOG.info("Dry-run succeeded. Telegram publishing remains disabled.")
            return 0

        before = last_publication(path)
        run_command("-m", "scripts.publish_longread", "--live")
        after = last_publication(path)
        if before != after:
            LOG.info("Longread published; skip ordinary news this invocation.")
            return 0
        run_command("main.py", "--scheduled-release", "--max-items", "1")
        LOG.info("Scheduled-release run complete")
        return 0
    except (OSError, ValueError, RuntimeError, requests.RequestException, sqlite3.DatabaseError) as error:
        LOG.error("Safe stop (%s): %s", type(error).__name__, str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
