"""End-to-end normalize -> derive against real captured pytorch/ao payloads.

The unit tests for each module use synthetic events; this file pins the numbers the real
pipeline actually produces, so a refactor that keeps every unit test green but changes a
reported metric still fails. Every expected value here was verified by hand against the
fixture timestamps.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pr_tracker.contracts import Config, PRRef
from pr_tracker.derive import derive
from pr_tracker.normalize import normalize

FIXTURES = Path(__file__).parent / "fixtures"

# Fixed so every assertion below is deterministic.
NOW = datetime(2026, 9, 17, 17, 0, 0, tzinfo=UTC)

# Mirrors the sample config/: the observed approvers, split so both classes are exercised.
CFG = Config(
    internal_reviewers=frozenset({"vkuzo", "andrewor14"}),
    external_reviewers=frozenset({"liangan1"}),
)


def run(number: int, cfg: Config = CFG):
    envelope = json.loads((FIXTURES / f"pytorch__ao__{number}.json").read_text())
    payload = envelope["data"]["repository"]["pullRequest"]
    ref = PRRef("pytorch", "ao", number)
    _meta, snapshot, events = normalize(payload, ref, fetched_at=NOW)
    milestones, metrics = derive(events, snapshot, cfg, NOW)
    return snapshot, events, milestones, metrics


def ts(text: str) -> datetime:
    return datetime.fromisoformat(text)


class TestDraftRoundTrip4893:
    """The PR that broke the original design rule: created ready, drafted 8s later,
    readied ~23h after, approved twice, merged."""

    def test_created_ready_not_as_draft(self):
        _s, _e, ms, mt = run(4893)
        # 2 draft transitions (even) + currently not a draft => created ready.
        assert ms.draft_at_creation is False
        assert ms.ready_at == ms.created_at == ts("2026-09-14T08:55:08+00:00")
        assert mt.hours_created_to_ready == 0.0

    def test_draft_interval_captured(self):
        _s, _e, ms, mt = run(4893)
        assert ms.draft_intervals == [
            (ts("2026-09-14T08:55:16+00:00"), ts("2026-09-15T08:02:46+00:00"))
        ]
        assert mt.hours_draft_total == 23.12

    def test_draft_hours_excluded_from_review_latency(self):
        """The whole point of 6.1. Wall clock to the external approval is 43.79h; 23.12h of
        that was draft time, so the reported latency is 20.66h, not 43.79h."""
        _s, _e, _ms, mt = run(4893)
        assert mt.ready_hours_to_external_approval == 20.66
        assert mt.ready_hours_to_first_review == 20.66
        assert mt.ready_hours_to_internal_approval == 32.67

    def test_both_reviewer_classes_resolved(self):
        _s, _e, ms, _mt = run(4893)
        assert ms.external_approved_at == ts("2026-09-16T04:42:16+00:00")  # liangan1
        assert ms.internal_approved_at == ts("2026-09-16T16:43:08+00:00")  # vkuzo
        assert ms.other_approved_at is None

    def test_merged_not_reported_as_closed(self):
        """MergedEvent and ClosedEvent share the same second here, and the (at, id) sort puts
        the close event FIRST. Ordering must not decide this — state must."""
        _s, events, ms, mt = run(4893)
        types_at_merge = [e.type for e in events if e.at == ts("2026-09-16T16:43:20+00:00")]
        assert sorted(types_at_merge) == ["closed", "merged"]
        assert ms.merged_at == ts("2026-09-16T16:43:20+00:00")
        assert ms.closed_at is None
        assert mt.wall_hours_to_merge == 55.8

    def test_review_counts(self):
        _s, _e, _ms, mt = run(4893)
        assert mt.changes_requested_count == 0
        assert mt.review_rounds == 2
        assert mt.distinct_reviewers == 2


class TestPlainMerged4896:
    def test_no_draft_time(self):
        _s, _e, ms, mt = run(4896)
        assert ms.draft_intervals == []
        assert mt.hours_draft_total == 0.0
        assert ms.ready_at == ms.created_at

    def test_single_internal_approval(self):
        _s, _e, ms, mt = run(4896)
        assert ms.internal_approved_at == ts("2026-09-16T16:41:52+00:00")
        assert ms.external_approved_at is None
        assert mt.ready_hours_to_internal_approval == 43.49
        assert mt.ready_hours_to_external_approval is None
        assert mt.review_rounds == 1

    def test_unknown_reviewer_falls_to_other(self):
        """With empty reviewer lists the same approval satisfies neither milestone."""
        _s, _e, ms, mt = run(4896, Config())
        assert ms.internal_approved_at is None
        assert ms.external_approved_at is None
        assert ms.other_approved_at == ts("2026-09-16T16:41:52+00:00")
        assert mt.ready_hours_to_internal_approval is None


class TestOpenChangesRequested4890:
    def test_submitted_at_wins_over_created_at(self):
        """This review's createdAt is 21:32:37 but submittedAt is 21:32:43 — a real 6s
        divergence, so the fallback order is load-bearing."""
        _s, _e, ms, _mt = run(4890)
        assert ms.first_review_at == ts("2026-09-16T21:32:43+00:00")
        assert ms.first_changes_requested_at == ts("2026-09-16T21:32:43+00:00")

    def test_unfinished_milestones_are_none_not_zero(self):
        _s, _e, ms, mt = run(4890)
        assert ms.merged_at is None
        assert ms.closed_at is None
        assert mt.wall_hours_to_merge is None
        assert mt.ready_hours_to_internal_approval is None
        assert mt.changes_requested_count == 1
        assert mt.ready_hours_to_first_review == 123.18
        assert mt.open_hours == 142.63


class TestStillDraft4908:
    """A brand-new draft with a completely empty timeline."""

    def test_only_the_synthesized_created_event(self):
        _s, events, _ms, _mt = run(4908)
        assert [e.type for e in events] == ["created"]

    def test_never_ready_nulls_review_metrics(self):
        _s, _e, ms, mt = run(4908)
        assert ms.draft_at_creation is True
        assert ms.ready_at is None
        assert mt.hours_created_to_ready is None
        assert mt.ready_hours_to_first_review is None
        assert mt.ready_hours_to_internal_approval is None
        assert mt.ready_hours_to_external_approval is None

    def test_open_ended_draft_interval(self):
        _s, _e, ms, mt = run(4908)
        assert len(ms.draft_intervals) == 1
        start, end = ms.draft_intervals[0]
        assert start == ts("2026-09-17T16:35:46+00:00")
        assert end is None  # still a draft; closed at `now` when measured
        assert mt.hours_draft_total == 0.4


class TestDeterminism:
    @pytest.mark.parametrize("number", [4893, 4896, 4890, 4908])
    def test_same_inputs_same_numbers(self, number):
        _s1, _e1, ms1, mt1 = run(number)
        _s2, _e2, ms2, mt2 = run(number)
        assert ms1 == ms2
        assert mt1 == mt2
