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

from datetime import datetime

from .contracts import DRAFT_TRANSITIONS, Config, Event, Metrics, Milestones, Snapshot
from .timeutil import Interval, close_intervals, hours, hours_between, hours_excluding

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

    reviews = [e for e in ordered if e.type == "review"]
    first_review_at = reviews[0].at if reviews else None
    first_changes_requested_at = next(
        (e.at for e in reviews if e.review_state == "CHANGES_REQUESTED"), None
    )
    approvals = _earliest_approvals(reviews, cfg)

    merged_at, closed_at = _terminal_timestamps(ordered, snapshot)

    milestones = Milestones(
        created_at=created_at,
        ready_at=ready_at,
        draft_at_creation=draft_at_creation,
        draft_intervals=draft_intervals,
        first_review_at=first_review_at,
        first_changes_requested_at=first_changes_requested_at,
        internal_approved_at=approvals.get("internal"),
        external_approved_at=approvals.get("external"),
        other_approved_at=approvals.get("other"),
        merged_at=merged_at,
        closed_at=closed_at,
    )

    metrics = Metrics(
        hours_draft_total=_hours_draft_total(draft_intervals, now),
        hours_created_to_ready=(None if ready_at is None else hours_between(created_at, ready_at)),
        ready_hours_to_first_review=_ready_hours(ready_at, first_review_at, draft_intervals, now),
        ready_hours_to_internal_approval=_ready_hours(
            ready_at, approvals.get("internal"), draft_intervals, now
        ),
        ready_hours_to_external_approval=_ready_hours(
            ready_at, approvals.get("external"), draft_intervals, now
        ),
        wall_hours_to_merge=(None if merged_at is None else hours_between(created_at, merged_at)),
        changes_requested_count=sum(1 for e in reviews if e.review_state == "CHANGES_REQUESTED"),
        review_rounds=sum(1 for e in reviews if e.review_state in _ROUND_STATES),
        distinct_reviewers=len({e.actor for e in reviews if e.actor}),
        open_hours=hours_between(created_at, merged_at or closed_at or now),
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
        return (merged[-1] if merged else None), None
    if snapshot.state == "CLOSED":
        closed = [e.at for e in ordered if e.type == "closed"]
        return None, (closed[-1] if closed else None)
    return None, None


def _hours_draft_total(draft_intervals: list[Interval], now: datetime) -> float:
    """Total draft hours, open intervals closed at `now`. Never a draft is a real 0.0."""
    closed = close_intervals(draft_intervals, now)
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
