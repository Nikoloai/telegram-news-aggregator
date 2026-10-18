from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from aggregator.longreads import LongreadPublisher, publish_due
from aggregator.storage import Storage
from aggregator.telegram import TelegramClient


def main() -> int:
    parser = argparse.ArgumentParser(description='Publish at most one approved longread')
    parser.add_argument('--live', action='store_true', help='Required for external writes')
    parser.add_argument('--immediate', action='store_true', help='Only explicit publish_immediately queue entries')
    args = parser.parse_args()
    storage = Storage(os.getenv('STATE_DB', 'data/state.db'))
    try:
        token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
        channel = os.getenv('TELEGRAM_CHANNEL', '@GVOZDIchKAAA').strip()
        publisher = None
        if args.live:
            if not token or channel != '@GVOZDIchKAAA':
                raise ValueError('Expected channel and bot token required')
            access = TelegramClient(token).check_channel_access(channel)
            if access.username != 'tnews_aggregator_bot' or not access.can_post_messages:
                raise ValueError('Unexpected bot identity or missing posting permission')
            publisher = LongreadPublisher(storage, token, channel)
        receipt = publish_due(Path('content/longreads'), storage, publisher, datetime.now(timezone.utc),
                              args.immediate, int(os.getenv('DAILY_POST_LIMIT', '10')))
        if receipt:
            safe = {key: value for key, value in receipt.items()
                    if key in {'status', 'slug', 'title', 'url', 'message_id', 'published_at'}}
            print(json.dumps(safe, ensure_ascii=True))
            if args.live and receipt.get('status') == 'published' and os.getenv('GITHUB_ENV'):
                with open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8') as stream:
                    stream.write('LONGREAD_PUBLISHED=true\n')
        else:
            print('No eligible longread; ordinary cadence unchanged.')
        return 0
    except Exception as error:
        # Remote exceptions may embed credentials; never expose repr/traceback.
        print(f'Longread stopped safely ({type(error).__name__}); check durable receipt before retrying.')
        return 1
    finally:
        storage.close()


if __name__ == '__main__':
    raise SystemExit(main())
