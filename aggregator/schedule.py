from __future__ import annotations

from datetime import datetime


# Ten separate messages, spread across the Moscow editorial day.
PUBLICATION_SLOTS = ((8, 17), (9, 47), (11, 17), (12, 47), (14, 17),
                     (15, 47), (17, 17), (18, 47), (20, 17), (22, 17))


def due_posts(local_now: datetime, target: int = 10) -> int:
    elapsed = sum((local_now.hour, local_now.minute) >= slot for slot in PUBLICATION_SLOTS)
    return elapsed * max(0, target) // len(PUBLICATION_SLOTS)


def weekly_due(local_now: datetime) -> bool:
    return local_now.weekday() == 6 and (local_now.hour, local_now.minute) >= (21, 32)
