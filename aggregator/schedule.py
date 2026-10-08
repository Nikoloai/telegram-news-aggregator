from __future__ import annotations

import hashlib
from datetime import date, datetime


# Nominal 72-minute spacing; each day's actual times vary by up to 13 minutes.
PUBLICATION_SLOTS = tuple(divmod(8 * 60 + 15 + index * 72, 60) for index in range(10))


def publication_slots(day: date) -> tuple[tuple[int, int], ...]:
    """Ten reproducible, uneven times inside the Moscow daytime window.

    A stable date-based seed prevents reruns from moving the goalposts.
    Neither process randomness nor wall-clock polling changes today's plan.
    """
    slots = []
    for index, (hour, minute) in enumerate(PUBLICATION_SLOTS):
        digest = hashlib.sha256(f"moscow-editorial-v1:{day.isoformat()}:{index}".encode()).digest()
        jitter = int.from_bytes(digest[:2], "big") % 27 - 13
        slots.append(divmod(hour * 60 + minute + jitter, 60))
    return tuple(slots)


def due_posts(local_now: datetime, target: int = 10) -> int:
    slots = publication_slots(local_now.date())
    elapsed = sum((local_now.hour, local_now.minute) >= slot for slot in slots)
    return elapsed * max(0, target) // len(slots)


def weekly_due(local_now: datetime) -> bool:
    return local_now.weekday() == 6 and (local_now.hour, local_now.minute) >= (21, 32)
