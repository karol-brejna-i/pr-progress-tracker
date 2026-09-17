"""Interval arithmetic tests. These cover the case that broke the original design rule:
a PR that re-enters draft after being ready, so a review window straddles draft time.
"""

from datetime import UTC, datetime

import pytest

from pr_tracker.timeutil import (
    format_ts,
    hours_between,
    hours_excluding,
    merge_intervals,
    overlap_seconds,
    parse_ts,
)


def t(day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, second, tzinfo=UTC)


class TestParsing:
    def test_parses_github_z_suffix(self):
        assert parse_ts("2026-09-14T08:55:08Z") == t(14, 8, 55, 8)

    def test_roundtrip(self):
        assert format_ts(parse_ts("2026-09-14T08:55:08Z")) == "2026-09-14T08:55:08Z"

    def test_offset_form_normalizes_to_utc(self):
        assert parse_ts("2026-09-14T10:55:08+02:00") == t(14, 8, 55, 8)


class TestHoursBetween:
    def test_simple_span(self):
        assert hours_between(t(1, 0), t(1, 6)) == 6.0

    def test_negative_span_clamps_to_zero(self):
        assert hours_between(t(2), t(1)) == 0.0

    def test_rounds_to_two_places(self):
        assert hours_between(t(1, 0, 0, 0), t(1, 0, 5, 0)) == 0.08


class TestMergeIntervals:
    def test_empty(self):
        assert merge_intervals([]) == []

    def test_unions_overlapping(self):
        assert merge_intervals([(t(1), t(3)), (t(2), t(5))]) == [(t(1), t(5))]

    def test_keeps_disjoint_sorted(self):
        assert merge_intervals([(t(6), t(7)), (t(1), t(2))]) == [(t(1), t(2)), (t(6), t(7))]

    def test_unions_adjacent(self):
        assert merge_intervals([(t(1), t(2)), (t(2), t(3))]) == [(t(1), t(3))]


class TestOverlap:
    """Each case from the design's interval checklist."""

    def test_interval_entirely_before_window(self):
        assert overlap_seconds(t(5), t(6), [(t(1), t(2))]) == 0.0

    def test_interval_entirely_after_window(self):
        assert overlap_seconds(t(1), t(2), [(t(5), t(6))]) == 0.0

    def test_partial_overlap_at_start(self):
        assert overlap_seconds(t(2), t(4), [(t(1), t(3))]) == 24 * 3600.0

    def test_partial_overlap_at_end(self):
        assert overlap_seconds(t(2), t(4), [(t(3), t(9))]) == 24 * 3600.0

    def test_window_fully_inside_interval(self):
        assert overlap_seconds(t(2), t(3), [(t(1), t(9))]) == 24 * 3600.0

    def test_multiple_intervals_in_one_window(self):
        got = overlap_seconds(t(1), t(10), [(t(2), t(3)), (t(5), t(6))])
        assert got == 48 * 3600.0

    def test_overlapping_intervals_counted_once(self):
        got = overlap_seconds(t(1), t(10), [(t(2), t(4)), (t(3), t(5))])
        assert got == 72 * 3600.0

    def test_zero_width_window(self):
        assert overlap_seconds(t(3), t(3), [(t(1), t(9))]) == 0.0

    def test_open_ended_interval_closed_at_now(self):
        got = overlap_seconds(t(1), t(10), [(t(2), None)], now=t(4))
        assert got == 48 * 3600.0


class TestHoursExcluding:
    def test_no_intervals_equals_wall_clock(self):
        assert hours_excluding(t(1), t(2), []) == 24.0

    def test_subtracts_draft_time(self):
        # 48h wall clock, 24h of it in draft -> 24 ready hours.
        assert hours_excluding(t(1), t(3), [(t(1), t(2))]) == 24.0

    def test_fully_draft_window_is_zero(self):
        assert hours_excluding(t(1), t(2), [(t(1), t(9))]) == 0.0

    def test_pytorch_ao_4893_shape(self):
        """Created ready, drafted 8s later, readied ~23h after, approved next day.

        The naive rule reported 0 draft hours here and charged the 23h to review latency.
        """
        created = datetime(2026, 9, 14, 8, 55, 8, tzinfo=UTC)
        drafted = datetime(2026, 9, 14, 8, 55, 16, tzinfo=UTC)
        readied = datetime(2026, 9, 15, 8, 2, 46, tzinfo=UTC)
        approved = datetime(2026, 9, 16, 4, 42, 16, tzinfo=UTC)

        draft_intervals = [(drafted, readied)]

        wall = hours_between(created, approved)
        ready = hours_excluding(created, approved, draft_intervals)

        # 43h47m08s wall clock, of which 23h07m30s was spent in draft.
        assert wall == pytest.approx(43.79, abs=0.01)
        assert ready == pytest.approx(20.66, abs=0.01)
        # The whole point: the draft time is actually excluded.
        assert wall - ready == pytest.approx(23.13, abs=0.01)

    def test_reversed_bounds_clamp(self):
        assert hours_excluding(t(5), t(1), []) == 0.0
