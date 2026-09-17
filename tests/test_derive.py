"""Derivation tests, organized around the edge cases docs/design.md 6.1 decides explicitly.

Events are built by hand: `derive` is pure, so no fixture or network is involved. The
interesting cases are the ones where a plausible-looking wrong rule still produces numbers —
draft parity, ready hours, a merged PR's extra `closed` event, dismissed approvals.
"""

from datetime import UTC, datetime, timedelta

import pytest

from pr_tracker.contracts import Config, Event, Snapshot
from pr_tracker.derive import derive

CFG = Config(
    internal_reviewers=frozenset({"bob"}),
    external_reviewers=frozenset({"carol"}),
)

CREATED = datetime(2026, 9, 10, 8, 0, 0, tzinfo=UTC)
NOW = datetime(2026, 9, 20, 8, 0, 0, tzinfo=UTC)


def t(hours_after: float) -> datetime:
    """A timestamp `hours_after` hours after `CREATED`."""
    return CREATED + timedelta(hours=hours_after)


def created_event(at: datetime = CREATED) -> Event:
    return Event(id="e-created", type="created", at=at, actor="alice")


def snap(state: str = "OPEN", *, is_draft: bool = False) -> Snapshot:
    return Snapshot(
        state=state,  # type: ignore[arg-type]
        is_draft=is_draft,
        additions=10,
        deletions=2,
        changed_files=1,
        fetched_at=NOW,
    )


def review(id_: str, at: datetime, actor: str, state: str) -> Event:
    return Event(id=id_, type="review", at=at, actor=actor, review_state=state)  # type: ignore[arg-type]


class TestNeverADraft:
    def test_ready_at_is_creation_and_no_draft_time(self):
        events = [created_event(), review("r1", t(4), "bob", "APPROVED")]
        ms, mx = derive(events, snap(), CFG, NOW)

        assert ms.draft_at_creation is False
        assert ms.draft_intervals == []
        assert ms.ready_at == CREATED
        # A PR that was ready at creation took zero hours to become ready. Not None.
        assert mx.hours_created_to_ready == 0.0
        assert mx.hours_draft_total == 0.0
        assert mx.ready_hours_to_internal_approval == 4.0

    def test_ready_hours_equal_wall_hours(self):
        events = [created_event(), review("r1", t(9), "bob", "COMMENTED")]
        _, mx = derive(events, snap(), CFG, NOW)
        assert mx.ready_hours_to_first_review == 9.0


class TestCreatedAsDraft:
    def test_parity_infers_creation_as_draft(self):
        events = [
            created_event(),
            Event(id="rfr", type="ready_for_review", at=t(24), actor="alice"),
            review("r1", t(30), "bob", "APPROVED"),
        ]
        ms, mx = derive(events, snap(), CFG, NOW)

        # One transition (odd) and currently not a draft -> created as a draft.
        assert ms.draft_at_creation is True
        assert ms.draft_intervals == [(CREATED, t(24))]
        assert ms.ready_at == t(24)
        assert mx.hours_created_to_ready == 24.0
        assert mx.hours_draft_total == 24.0
        # 30h wall clock from creation, but only 6 of them reviewable.
        assert mx.ready_hours_to_internal_approval == 6.0

    def test_fresh_draft_with_zero_events_has_no_ready_metrics(self):
        ms, mx = derive([created_event()], snap(is_draft=True), CFG, NOW)

        # 0 transitions (even) XOR is_draft=True -> created as a draft, still one.
        assert ms.draft_at_creation is True
        assert ms.ready_at is None
        assert ms.draft_intervals == [(CREATED, None)]
        assert mx.hours_created_to_ready is None
        assert mx.ready_hours_to_first_review is None
        assert mx.ready_hours_to_internal_approval is None
        assert mx.ready_hours_to_external_approval is None
        # Open-ended draft interval is closed at `now`: 10 days.
        assert mx.hours_draft_total == 240.0
        assert mx.open_hours == 240.0

    def test_still_open_draft_after_being_ready(self):
        """Ready, then back to draft and left there — the open interval runs to `now`."""
        events = [
            created_event(),
            Event(id="rfr", type="ready_for_review", at=t(2), actor="alice"),
            Event(id="ctd", type="convert_to_draft", at=t(5), actor="alice"),
        ]
        ms, mx = derive(events, snap(is_draft=True), CFG, NOW)

        # Two transitions (even) XOR is_draft=True -> created as a draft.
        assert ms.draft_at_creation is True
        assert ms.draft_intervals == [(CREATED, t(2)), (t(5), None)]
        assert ms.ready_at == t(2)
        assert mx.hours_draft_total == pytest.approx(2.0 + 235.0)
        assert mx.open_hours == 240.0
        assert ms.merged_at is None and ms.closed_at is None

    def test_closed_while_still_draft_stops_accruing_at_close(self):
        """Regression: an open draft interval must close at closed_at, not at `now` — a PR
        that has stopped existing cannot keep accumulating draft hours against the clock."""
        events = [created_event(), Event(id="c", type="closed", at=t(12), actor="alice")]
        ms, mx = derive(events, snap("CLOSED", is_draft=True), CFG, NOW)

        assert ms.draft_intervals == [(CREATED, None)]
        assert ms.closed_at == t(12)
        # NOW is 240h after CREATED; if the bug were still present this would be 240.0.
        assert mx.hours_draft_total == 12.0
        assert mx.open_hours == 12.0

    def test_merged_while_still_draft_stops_accruing_at_merge(self):
        events = [created_event(), Event(id="m", type="merged", at=t(8), actor="alice")]
        ms, mx = derive(events, snap("MERGED", is_draft=True), CFG, NOW)

        assert ms.merged_at == t(8)
        assert mx.hours_draft_total == 8.0
        assert mx.open_hours == 8.0


class TestPytorchAo4893RoundTrip:
    """Created ready, converted to draft 8s later, readied ~23h after, approved next day.

    A rule that inspects only the first transition calls this PR "created as a draft" and
    reports zero draft hours, charging the 23 draft hours to review latency.
    """

    created = datetime(2026, 9, 14, 8, 55, 8, tzinfo=UTC)
    drafted = datetime(2026, 9, 14, 8, 55, 16, tzinfo=UTC)
    readied = datetime(2026, 9, 15, 8, 2, 46, tzinfo=UTC)
    approved = datetime(2026, 9, 16, 4, 42, 16, tzinfo=UTC)

    def events(self) -> list[Event]:
        return [
            created_event(self.created),
            Event(id="ctd", type="convert_to_draft", at=self.drafted, actor="alice"),
            Event(id="rfr", type="ready_for_review", at=self.readied, actor="alice"),
            review("r1", self.approved, "bob", "APPROVED"),
        ]

    def test_created_ready_despite_two_transitions(self):
        ms, _ = derive(self.events(), snap(), CFG, NOW)
        # 2 transitions (even) XOR is_draft=False -> created ready.
        assert ms.draft_at_creation is False
        assert ms.ready_at == self.created
        assert ms.draft_intervals == [(self.drafted, self.readied)]

    def test_draft_time_is_excluded_from_the_approval_metric(self):
        _, mx = derive(self.events(), snap(), CFG, NOW)
        wall = 43.79
        assert mx.hours_draft_total == pytest.approx(23.13, abs=0.01)
        assert mx.ready_hours_to_internal_approval == pytest.approx(20.66, abs=0.01)
        assert wall - mx.ready_hours_to_internal_approval == pytest.approx(23.13, abs=0.01)

    def test_hours_created_to_ready_is_zero_not_the_draft_span(self):
        """It was ready at creation; the later draft dip belongs to `hours_draft_total`."""
        _, mx = derive(self.events(), snap(), CFG, NOW)
        assert mx.hours_created_to_ready == 0.0


class TestMergeAndClose:
    def test_merged_pr_also_emits_closed_event(self):
        events = [
            created_event(),
            Event(id="m", type="merged", at=t(50), actor="bob"),
            Event(id="c", type="closed", at=t(50) + timedelta(seconds=1), actor="bob"),
        ]
        ms, mx = derive(events, snap("MERGED"), CFG, NOW)

        assert ms.merged_at == t(50)
        # The trailing close event must not present this as "closed without merging".
        assert ms.closed_at is None
        assert mx.wall_hours_to_merge == 50.0
        assert mx.open_hours == 50.0

    def test_merged_with_no_approval_keeps_milestones_none(self):
        events = [
            created_event(),
            review("r1", t(3), "bob", "COMMENTED"),
            Event(id="m", type="merged", at=t(4), actor="alice"),
            Event(id="c", type="closed", at=t(4) + timedelta(seconds=1), actor="alice"),
        ]
        ms, mx = derive(events, snap("MERGED"), CFG, NOW)

        assert ms.internal_approved_at is None
        assert ms.external_approved_at is None
        assert ms.other_approved_at is None
        # An admin merge is not "approved in 0 hours".
        assert mx.ready_hours_to_internal_approval is None
        assert mx.wall_hours_to_merge == 4.0
        assert mx.review_rounds == 0
        assert mx.distinct_reviewers == 1

    def test_closed_without_merging(self):
        events = [created_event(), Event(id="c", type="closed", at=t(12), actor="alice")]
        ms, mx = derive(events, snap("CLOSED"), CFG, NOW)

        assert ms.merged_at is None
        assert ms.closed_at == t(12)
        assert mx.wall_hours_to_merge is None
        assert mx.open_hours == 12.0

    def test_reopened_then_closed_uses_latest_close(self):
        events = [
            created_event(),
            Event(id="c1", type="closed", at=t(5), actor="alice"),
            Event(id="ro", type="reopened", at=t(6), actor="alice"),
            Event(id="c2", type="closed", at=t(20), actor="alice"),
        ]
        ms, _ = derive(events, snap("CLOSED"), CFG, NOW)
        assert ms.closed_at == t(20)

    def test_reopened_and_open_has_no_closed_at(self):
        events = [
            created_event(),
            Event(id="c1", type="closed", at=t(5), actor="alice"),
            Event(id="ro", type="reopened", at=t(6), actor="alice"),
        ]
        ms, mx = derive(events, snap("OPEN"), CFG, NOW)
        assert ms.closed_at is None
        assert mx.open_hours == 240.0


class TestApprovals:
    def test_dismissed_approval_still_counts_as_reached(self):
        """The stored history keeps the original APPROVED review; the dismissal is separate."""
        events = [
            created_event(),
            review("r1", t(6), "bob", "APPROVED"),
            Event(id="rd", type="review_dismissed", at=t(7), actor="alice"),
            review("r2", t(8), "bob", "CHANGES_REQUESTED"),
        ]
        ms, mx = derive(events, snap(), CFG, NOW)

        assert ms.internal_approved_at == t(6)
        assert mx.ready_hours_to_internal_approval == 6.0
        assert mx.changes_requested_count == 1
        assert mx.review_rounds == 2

    def test_review_recorded_as_dismissed_is_not_an_approval(self):
        """A re-fetch reports the dismissed review with state DISMISSED, not APPROVED."""
        events = [
            created_event(),
            review("r1", t(6), "bob", "DISMISSED"),
            review("r2", t(9), "bob", "APPROVED"),
        ]
        ms, mx = derive(events, snap(), CFG, NOW)

        assert ms.internal_approved_at == t(9)
        assert ms.first_review_at == t(6)
        assert mx.review_rounds == 2

    def test_other_reviewer_approval_satisfies_neither_milestone(self):
        events = [created_event(), review("r1", t(3), "dave", "APPROVED")]
        ms, mx = derive(events, snap(), CFG, NOW)

        assert ms.other_approved_at == t(3)
        assert ms.internal_approved_at is None
        assert ms.external_approved_at is None
        assert mx.ready_hours_to_internal_approval is None
        assert mx.ready_hours_to_external_approval is None

    def test_stored_reviewer_class_is_ignored(self):
        """Classification is recomputed, so a stale stored class must not leak through."""
        stale = review("r1", t(3), "dave", "APPROVED")
        stale.reviewer_class = "internal"
        ms, _ = derive([created_event(), stale], snap(), CFG, NOW)
        assert ms.internal_approved_at is None
        assert ms.other_approved_at == t(3)

    def test_internal_and_external_approvals_both_present(self):
        events = [
            created_event(),
            review("r1", t(2), "carol", "COMMENTED"),
            review("r2", t(5), "carol", "APPROVED"),
            review("r3", t(11), "bob", "APPROVED"),
            review("r4", t(14), "bob", "APPROVED"),
        ]
        ms, mx = derive(events, snap(), CFG, NOW)

        assert ms.external_approved_at == t(5)
        # Earliest approval per class wins, not the latest.
        assert ms.internal_approved_at == t(11)
        assert mx.ready_hours_to_external_approval == 5.0
        assert mx.ready_hours_to_internal_approval == 11.0
        assert mx.distinct_reviewers == 2
        assert mx.review_rounds == 3
        assert mx.changes_requested_count == 0

    def test_case_insensitive_and_at_prefixed_logins_classify(self):
        events = [created_event(), review("r1", t(2), "@BOB", "APPROVED")]
        ms, _ = derive(events, snap(), CFG, NOW)
        assert ms.internal_approved_at == t(2)


class TestReviewCounters:
    def test_commented_only_reviews_are_not_rounds_but_do_start_the_clock(self):
        events = [
            created_event(),
            review("r1", t(1), "bob", "COMMENTED"),
            review("r2", t(2), "dave", "COMMENTED"),
        ]
        ms, mx = derive(events, snap(), CFG, NOW)

        assert ms.first_review_at == t(1)
        assert mx.ready_hours_to_first_review == 1.0
        assert mx.review_rounds == 0
        assert mx.distinct_reviewers == 2

    def test_first_changes_requested_and_count(self):
        events = [
            created_event(),
            review("r1", t(1), "bob", "CHANGES_REQUESTED"),
            review("r2", t(4), "carol", "CHANGES_REQUESTED"),
        ]
        ms, mx = derive(events, snap(), CFG, NOW)

        assert ms.first_changes_requested_at == t(1)
        assert mx.changes_requested_count == 2

    def test_anonymous_reviewer_is_not_counted_as_distinct(self):
        events = [created_event(), review("r1", t(1), "bob", "APPROVED")]
        events.append(Event(id="r2", type="review", at=t(2), actor=None, review_state="COMMENTED"))
        _, mx = derive(events, snap(), CFG, NOW)
        assert mx.distinct_reviewers == 1

    def test_non_review_events_are_not_reviews(self):
        events = [
            created_event(),
            Event(id="rr", type="review_requested", at=t(0.001), actor="alice"),
            Event(id="rrr", type="review_request_removed", at=t(0.002), actor="alice"),
        ]
        ms, mx = derive(events, snap(), CFG, NOW)
        assert ms.first_review_at is None
        assert mx.ready_hours_to_first_review is None
        assert mx.review_rounds == 0
        assert mx.distinct_reviewers == 0


class TestOrderingAndPurity:
    def test_events_out_of_chronological_order(self):
        ordered = [
            created_event(),
            Event(id="ctd", type="convert_to_draft", at=t(1), actor="alice"),
            Event(id="rfr", type="ready_for_review", at=t(9), actor="alice"),
            review("r1", t(10), "bob", "APPROVED"),
            Event(id="m", type="merged", at=t(12), actor="bob"),
        ]
        shuffled = [ordered[3], ordered[0], ordered[4], ordered[2], ordered[1]]

        from_ordered = derive(ordered, snap("MERGED"), CFG, NOW)
        from_shuffled = derive(shuffled, snap("MERGED"), CFG, NOW)

        assert from_ordered == from_shuffled
        ms, mx = from_shuffled
        assert ms.draft_intervals == [(t(1), t(9))]
        assert mx.ready_hours_to_internal_approval == 2.0
        assert mx.wall_hours_to_merge == 12.0

    def test_input_list_is_not_mutated(self):
        events = [
            review("r1", t(10), "bob", "APPROVED"),
            created_event(),
        ]
        before = list(events)
        derive(events, snap(), CFG, NOW)
        assert events == before

    def test_now_is_the_only_clock(self):
        """Same events, different `now` -> only the open-ended durations move."""
        events = [created_event()]
        _, early = derive(events, snap(is_draft=True), CFG, t(10))
        _, late = derive(events, snap(is_draft=True), CFG, t(20))
        assert early.hours_draft_total == 10.0
        assert late.hours_draft_total == 20.0
        assert early.open_hours == 10.0
        assert late.open_hours == 20.0


class TestDefensiveCases:
    def test_duplicate_convert_to_draft_does_not_open_two_intervals(self):
        """Malformed input: two same-direction transitions keep parity even, so the PR reads as
        created-as-draft and both events are no-ops. One interval, no double-counted hours.
        """
        events = [
            created_event(),
            Event(id="c1", type="convert_to_draft", at=t(1), actor="alice"),
            Event(id="c2", type="convert_to_draft", at=t(2), actor="alice"),
        ]
        ms, mx = derive(events, snap(is_draft=True), CFG, NOW)
        assert ms.draft_intervals == [(CREATED, None)]
        assert mx.hours_draft_total == 240.0

    def test_approval_before_ready_clamps_instead_of_going_negative(self):
        events = [
            created_event(),
            review("r1", t(1), "bob", "APPROVED"),
            Event(id="rfr", type="ready_for_review", at=t(5), actor="alice"),
        ]
        ms, mx = derive(events, snap(), CFG, NOW)
        assert ms.ready_at == t(5)
        assert mx.ready_hours_to_internal_approval == 0.0

    def test_missing_created_event_falls_back_to_earliest_event(self):
        events = [review("r1", t(3), "bob", "APPROVED")]
        ms, _ = derive(events, snap(), CFG, NOW)
        assert ms.created_at == t(3)

    def test_empty_event_list_falls_back_to_fetched_at(self):
        ms, mx = derive([], snap(), CFG, NOW)
        assert ms.created_at == NOW
        assert ms.ready_at == NOW
        assert mx.open_hours == 0.0
        assert mx.hours_draft_total == 0.0

    def test_merged_state_without_a_merged_event_logs_a_warning(self, caplog):
        """A truncated timeline (missing page, lost event) must not fail silently."""
        events = [created_event()]
        with caplog.at_level("WARNING"):
            ms, _ = derive(events, snap("MERGED"), CFG, NOW)
        assert ms.merged_at is None
        assert "no 'merged' event" in caplog.text

    def test_closed_state_without_a_closed_event_logs_a_warning(self, caplog):
        events = [created_event()]
        with caplog.at_level("WARNING"):
            ms, _ = derive(events, snap("CLOSED"), CFG, NOW)
        assert ms.closed_at is None
        assert "no 'closed' event" in caplog.text

    def test_replay_state_disagreeing_with_snapshot_logs_a_warning(self, caplog):
        """A lost ConvertToDraftEvent leaves the replay ending "not in draft" while the
        snapshot says otherwise — the invariant _draft_intervals's docstring promises."""
        events = [
            created_event(),
            Event(id="rfr", type="ready_for_review", at=t(2), actor="alice"),
        ]
        with caplog.at_level("WARNING"):
            derive(events, snap(is_draft=True), CFG, NOW)
        assert "draft state mismatch" in caplog.text

    def test_consistent_draft_state_logs_no_warning(self, caplog):
        with caplog.at_level("WARNING"):
            derive([created_event()], snap(is_draft=False), CFG, NOW)
        assert caplog.text == ""
