"""Milestone and metric derivation. Pure: everything is computed from the arguments.

This is the correctness core described in docs/design.md 6 and 6.1. Two rules carry most of
the risk and are therefore spelled out here:

* **Draft parity.** GitHub never says whether a PR was *created* as a draft, only its current
  `isDraft` plus the transition events. Every transition flips the state, so the initial state
  is recoverable as `is_draft XOR (number of transitions is odd)`. Looking at the *first*
  transition instead is wrong: pytorch/ao#4893 was created ready, converted to draft 8s later
  and readied ~23h after, which a first-transition rule scores as "created as draft, ready
  after 8 seconds" and then charges the 23 draft hours to review latency.
* **Ready hours.** Review-side durations subtract any overlap with the draft intervals, via
  `timeutil.hours_excluding`. `wall_hours_to_merge` deliberately stays wall-clock.

No `datetime.now()` anywhere: `now` is injected so a record derived twice from the same data
yields the same numbers.
"""

from __future__ import annotations

import logging
from datetime import datetime

from .contracts import (
    DRAFT_TRANSITIONS,
    ApprovalPath,
    Config,
    Event,
    Metrics,
    Milestones,
    ReviewerClass,
    Snapshot,
)
from .timeutil import Interval, close_intervals, hours, hours_between, hours_excluding

logger = logging.getLogger(__name__)

# Reviews that count as a "round" of review. A COMMENTED review is a drive-by note (and the
# state GitHub gives a review that only carries inline comments), so counting it would inflate
# the round count for chatty PRs; a DISMISSED review *was* a substantive review whose verdict
# was later thrown away, so it stays.
_ROUND_STATES = frozenset({"APPROVED", "CHANGES_REQUESTED", "DISMISSED"})


def derive(
    events: list[Event],
    snapshot: Snapshot,
    cfg: Config,
    now: datetime,
) -> tuple[Milestones, Metrics]:
    """Reduce an event list plus the current snapshot to milestones and metrics.

    `events` need not be sorted and is never mutated. Reviewer classification is recomputed
    from `cfg` on every call, so `Event.reviewer_class` as stored is ignored — editing the
    reviewer lists takes effect on the next read.
    """
    ordered = sorted(events, key=lambda e: (e.at, e.id))

    created_at = _created_at(ordered, snapshot)
    draft_at_creation = _draft_at_creation(ordered, snapshot)
    draft_intervals = _draft_intervals(ordered, created_at, draft_at_creation)
    ready_at = _ready_at(ordered, created_at, draft_at_creation)

    # The replay's end state should always agree with the snapshot's current is_draft — that
    # invariant is the only thing standing between a truncated/lost event and a silently wrong
    # draft_intervals. A mismatch means the timeline is incomplete or out of sync; log it
    # rather than let it pass as a real 0.0 or an interval that never closes.
    ends_in_draft = bool(draft_intervals) and draft_intervals[-1][1] is None
    if ends_in_draft != snapshot.is_draft:
        logger.warning(
            "draft state mismatch: replay ends %s but snapshot.is_draft=%s "
            "(incomplete or out-of-order timeline?)",
            "in draft" if ends_in_draft else "not in draft",
            snapshot.is_draft,
        )

    reviews = [e for e in ordered if e.type == "review"]
    first_review_at = reviews[0].at if reviews else None
    first_internal_review_at = _first_review_by_class(reviews, cfg, "internal")
    first_external_review_at = _first_review_by_class(reviews, cfg, "external")
    first_changes_requested_at = next(
        (e.at for e in reviews if e.review_state == "CHANGES_REQUESTED"), None
    )
    approvals = _earliest_approvals(reviews, cfg)
    approval_path = approval_path_of(approvals.get("internal"), approvals.get("external"))

    merged_at, closed_at = _terminal_timestamps(ordered, snapshot)
    # A PR that is closed/merged has stopped existing; an open-ended draft interval must
    # close at that moment, not keep accruing draft hours against a clock that has moved on.
    as_of = merged_at or closed_at or now

    milestones = Milestones(
        created_at=created_at,
        ready_at=ready_at,
        draft_at_creation=draft_at_creation,
        draft_intervals=draft_intervals,
        first_review_at=first_review_at,
        first_internal_review_at=first_internal_review_at,
        first_external_review_at=first_external_review_at,
        first_changes_requested_at=first_changes_requested_at,
        internal_approved_at=approvals.get("internal"),
        external_approved_at=approvals.get("external"),
        other_approved_at=approvals.get("other"),
        approval_path=approval_path,
        merged_at=merged_at,
        closed_at=closed_at,
    )

    metrics = Metrics(
        hours_draft_total=_hours_draft_total(draft_intervals, as_of),
        hours_created_to_ready=(None if ready_at is None else hours_between(created_at, ready_at)),
        ready_hours_to_first_review=_ready_hours(ready_at, first_review_at, draft_intervals, now),
        ready_hours_to_internal_review=_ready_hours(
            ready_at, first_internal_review_at, draft_intervals, now
        ),
        ready_hours_to_external_review=_ready_hours(
            ready_at, first_external_review_at, draft_intervals, now
        ),
        ready_hours_to_internal_approval=_ready_hours(
            ready_at, approvals.get("internal"), draft_intervals, now
        ),
        ready_hours_to_external_approval=_ready_hours(
            ready_at, approvals.get("external"), draft_intervals, now
        ),
        ready_hours_internal_to_external_approval=_handoff_hours(
            approval_path,
            approvals.get("internal"),
            approvals.get("external"),
            draft_intervals,
            now,
        ),
        wall_hours_to_merge=(None if merged_at is None else hours_between(created_at, merged_at)),
        changes_requested_count=sum(1 for e in reviews if e.review_state == "CHANGES_REQUESTED"),
        review_rounds=sum(1 for e in reviews if e.review_state in _ROUND_STATES),
        distinct_reviewers=len({e.actor for e in reviews if e.actor}),
        open_hours=hours_between(created_at, as_of),
    )
    return milestones, metrics


def _created_at(ordered: list[Event], snapshot: Snapshot) -> datetime:
    """`created_at` from the synthesized `created` event.

    normalize.py always synthesizes one; the fallbacks keep a partial event list from
    producing a crash rather than a slightly-off number.
    """
    for event in ordered:
        if event.type == "created":
            return event.at
    return ordered[0].at if ordered else snapshot.fetched_at


def _draft_at_creation(ordered: list[Event], snapshot: Snapshot) -> bool:
    transitions = sum(1 for e in ordered if e.type in DRAFT_TRANSITIONS)
    return snapshot.is_draft != (transitions % 2 == 1)


def _draft_intervals(
    ordered: list[Event],
    created_at: datetime,
    draft_at_creation: bool,
) -> list[Interval]:
    """Forward replay of the draft state machine, oldest event first.

    Transitions that do not change the state are ignored (GitHub should not emit them, but a
    duplicated event must not open a second interval). The final interval is left open-ended
    when the replay ends in draft; `timeutil` closes those at `now`. By the parity rule above
    that end state equals `snapshot.is_draft` for consistent data.
    """
    intervals: list[Interval] = []
    is_draft = draft_at_creation
    if is_draft:
        intervals.append((created_at, None))
    for event in ordered:
        if event.type == "convert_to_draft" and not is_draft:
            is_draft = True
            intervals.append((event.at, None))
        elif event.type == "ready_for_review" and is_draft:
            is_draft = False
            start, _ = intervals[-1]
            intervals[-1] = (start, event.at)
    return intervals


def _ready_at(
    ordered: list[Event],
    created_at: datetime,
    draft_at_creation: bool,
) -> datetime | None:
    """First moment the PR was in the ready state — `None` if it never was."""
    if not draft_at_creation:
        return created_at
    return next((e.at for e in ordered if e.type == "ready_for_review"), None)


def _earliest_approvals(reviews: list[Event], cfg: Config) -> dict[str, datetime]:
    """Earliest APPROVED review per reviewer class.

    A review recorded as DISMISSED is not itself an approval, but a dismissal never erases an
    approval that is still present in the history: the PR *did* reach approval once. `other`
    approvals fill `other_approved_at` only and satisfy neither approval milestone.
    """
    earliest: dict[str, datetime] = {}
    for event in reviews:
        if event.review_state != "APPROVED":
            continue
        cls = cfg.classify(event.actor)
        if cls not in earliest or event.at < earliest[cls]:
            earliest[cls] = event.at
    return earliest


def _first_review_by_class(
    reviews: list[Event], cfg: Config, want: ReviewerClass
) -> datetime | None:
    """Earliest review of any state by a reviewer in `want`'s class.

    Matches `first_review_at`'s "any non-PENDING state" semantics — a CHANGES_REQUESTED or a
    COMMENTED review is still the moment that side of the pipeline engaged with the PR.
    """
    return next((e.at for e in reviews if cfg.classify(e.actor) == want), None)


def approval_path_of(
    internal_at: datetime | None,
    external_at: datetime | None,
) -> ApprovalPath:
    """Classify the observed approval sequence. See `contracts.ApprovalPath`.

    Public because `report.py` classifies from the timestamps too rather than trusting the
    stored `Milestones.approval_path`: a record written before the field existed carries the
    default `"none"`, and a report that silently omits such a PR from its attention list is
    the exact failure this project treats as worse than a loud one.
    """
    if internal_at is None and external_at is None:
        return "none"
    if external_at is None:
        return "internal_only"
    if internal_at is None:
        return "external_only"
    return "internal_first" if internal_at <= external_at else "external_first"


def _handoff_hours(
    approval_path: ApprovalPath,
    internal_at: datetime | None,
    external_at: datetime | None,
    draft_intervals: list[Interval],
    now: datetime,
) -> float | None:
    """Ready hours from internal sign-off to maintainer approval.

    Deliberately `None` unless the pipeline actually ran in order. For `external_first` the
    elapsed time is negative and would clamp to 0.0, which reads as "handed off instantly"
    when the truth is that the maintainer approved before our team did — a different fact,
    and one `approval_path` already records. Simultaneous approvals classify as
    `internal_first` and give a real 0.0, which is what happened.
    """
    # The None checks are unreachable via `approval_path_of` (which only returns
    # "internal_first" with both timestamps present) and are here to narrow the types.
    if approval_path != "internal_first" or internal_at is None or external_at is None:
        return None
    return hours_excluding(internal_at, external_at, draft_intervals, now=now)


def _terminal_timestamps(
    ordered: list[Event],
    snapshot: Snapshot,
) -> tuple[datetime | None, datetime | None]:
    """`(merged_at, closed_at)` for the PR's *current* state.

    A merged PR also emits a `closed` event ~1s after `merged`, so MERGED is checked first and
    close events are ignored for it. A reopened-then-closed PR takes its latest close event.
    """
    if snapshot.state == "MERGED":
        merged = [e.at for e in ordered if e.type == "merged"]
        if not merged:
            logger.warning("snapshot.state=MERGED but no 'merged' event in the timeline")
        return (merged[-1] if merged else None), None
    if snapshot.state == "CLOSED":
        closed = [e.at for e in ordered if e.type == "closed"]
        if not closed:
            logger.warning("snapshot.state=CLOSED but no 'closed' event in the timeline")
        return None, (closed[-1] if closed else None)
    return None, None


def _hours_draft_total(draft_intervals: list[Interval], as_of: datetime) -> float:
    """Total draft hours, open intervals closed at `as_of`. Never a draft is a real 0.0.

    `as_of` is `merged_at or closed_at or now` (see `derive`), not always `now`: a PR closed
    or merged while still a draft must stop accruing draft hours at that moment, or the
    number keeps growing after the PR has stopped existing and, once `tracking == "final"`,
    is frozen wrong forever.
    """
    closed = close_intervals(draft_intervals, as_of)
    return hours(sum((end - start).total_seconds() for start, end in closed))


def _ready_hours(
    ready_at: datetime | None,
    target: datetime | None,
    draft_intervals: list[Interval],
    now: datetime,
) -> float | None:
    """Ready hours from `ready_at` to `target`, or `None` if either end never happened."""
    if ready_at is None or target is None:
        return None
    return hours_excluding(ready_at, target, draft_intervals, now=now)
