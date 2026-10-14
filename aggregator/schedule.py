from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta


# Nominal 72-minute spacing; each day's actual times vary by up to 13 minutes.
PUBLICATION_SLOTS = tuple(divmod(8 * 60 + 15 + index * 72, 60) for index in range(10))
MIN_POST_INTERVAL = timedelta(hours=1)


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


def spacing_ready(now: datetime, last_publication: datetime | None) -> bool:
    return last_publication is None or now - last_publication >= MIN_POST_INTERVAL


def regular_release_due(local_now: datetime, last_publication: datetime | None) -> bool:
    """Only the current day's latest slot is eligible; missed slots never queue up."""
    if local_now.hour >= 20 or not spacing_ready(local_now, last_publication):
        return False
    elapsed = [(hour, minute) for hour, minute in publication_slots(local_now.date())
               if (hour, minute) <= (local_now.hour, local_now.minute)]
    if not elapsed:
        return False
    hour, minute = elapsed[-1]
    slot_start = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # Any actual publication fills this slot, including a quote or urgent update.
    return last_publication is None or last_publication < slot_start
