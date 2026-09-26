"""Spaced-repetition scheduling for notebook cards.

A card climbs one level each time it is remembered and drops to level 0 when it is not.
The level decides how many days pass before the card is due again.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

GAPS_DAYS = (0, 1, 3, 7, 16, 35)
MAX_LEVEL = len(GAPS_DAYS) - 1
KNOWN_LEVEL = 3


def start_of_day(ts: float) -> float:
    d = datetime.fromtimestamp(ts)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def schedule(level: int, remembered: bool, now: float | None = None) -> tuple[int, float]:
    """Return (new_level, due_at) after one review."""
    now = time.time() if now is None else now
    if not remembered:
        return 0, now
    new_level = min(level + 1, MAX_LEVEL)
    due = datetime.fromtimestamp(start_of_day(now)) + timedelta(days=GAPS_DAYS[new_level])
    return new_level, due.timestamp()


def is_due(due_at: float | None, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    return due_at is None or due_at <= now
