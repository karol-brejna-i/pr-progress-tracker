"""Timestamp parsing and interval arithmetic.

Owned by the main session, shared by every module. The interval helpers here exist because
review-side metrics are measured in *ready hours* — wall-clock elapsed minus any overlap
with the PR's draft intervals (see docs/design.md 6.1). Getting that subtraction wrong is
how you silently report zero draft time for a PR that sat in draft for a day.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# An interval whose end may be None, meaning "still open at time of observation".
Interval = tuple[datetime, datetime | None]
ClosedInterval = tuple[datetime, datetime]


def parse_ts(value: str) -> datetime:
    """Parse a GitHub ISO-8601 timestamp into an aware UTC datetime."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def format_ts(value: datetime) -> str:
    """Render an aware datetime as a GitHub-style ISO-8601 UTC string."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def hours(seconds: float) -> float:
    """Seconds to hours, rounded to 2dp, clamped at zero. Logs when the clamp actually fires."""
    if seconds < 0:
        logger.warning(
            "negative duration %.2fs clamped to 0 (clock skew or out-of-order data?)", seconds
        )
    return round(max(seconds, 0.0) / 3600.0, 2)


def hours_between(start: datetime, end: datetime) -> float:
    """Wall-clock hours from start to end. Negative spans clamp to 0.0 and are logged.

    Clock skew and out-of-order event data do occur; a clamped zero is wrong in a visible
    way, whereas a negative duration corrupts every aggregate downstream.
    """
    return hours((end - start).total_seconds())


def close_intervals(intervals: list[Interval], until: datetime) -> list[ClosedInterval]:
    """Replace open-ended intervals (end is None) with `until`, dropping empty ones."""
    closed: list[ClosedInterval] = []
    for start, end in intervals:
        real_end = until if end is None else end
        if real_end > start:
            closed.append((start, real_end))
    return closed


def merge_intervals(intervals: list[ClosedInterval]) -> list[ClosedInterval]:
    """Sort and union overlapping/adjacent intervals.

    Draft intervals from a correct state machine never overlap, so this is defensive — but
    double-counting an overlap would subtract the same hours twice and understate elapsed
    time, which is a far more confusing failure than a redundant merge.
    """
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def overlap_seconds(
    window_start: datetime,
    window_end: datetime,
    intervals: list[Interval],
    *,
    now: datetime | None = None,
) -> float:
    """Total seconds of [window_start, window_end] covered by `intervals`.

    Open-ended intervals are closed at `now` (required if any interval is open).
    """
    if window_end <= window_start:
        return 0.0
    fallback_end = now if now is not None else window_end
    closed = close_intervals(intervals, fallback_end)
    total = 0.0
    for start, end in merge_intervals(closed):
        lo = max(window_start, start)
        hi = min(window_end, end)
        if hi > lo:
            total += (hi - lo).total_seconds()
    return total


def hours_excluding(
    start: datetime,
    end: datetime,
    intervals: list[Interval],
    *,
    now: datetime | None = None,
) -> float:
    """Hours from start to end, excluding any time inside `intervals` ("ready hours")."""
    if end <= start:
        return 0.0
    span = (end - start).total_seconds()
    return hours(span - overlap_seconds(start, end, intervals, now=now))
