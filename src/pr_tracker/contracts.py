"""Shared types and the module manifest. Owned by the main session — do not edit.

This is the parallelization seam: module agents code *to* these types and report mismatches
upward rather than changing them locally. See AGENTS.md.

In-memory representation uses aware UTC `datetime`. The JSON on disk uses ISO-8601 strings
with a trailing `Z`; `store.py` owns that conversion at the boundary, via
`timeutil.parse_ts` / `timeutil.format_ts`. No other module should convert timestamps.

Required public functions per module (implement exactly these names and signatures):

    github.py
        fetch_pull_request(ref: PRRef, token: str) -> dict
            Returns the UNWRAPPED `data.repository.pullRequest` object, with all
            `timelineItems` pages already followed and concatenated into a single
            `timelineItems.nodes` list. Raises FetchError.
        class FetchError(Exception)  # carries a human-readable one-line reason

    normalize.py
        normalize(payload: dict, ref: PRRef) -> tuple[PRMeta, Snapshot, list[Event]]
            `payload` is the unwrapped `pullRequest` object described above — NOT the raw
            `{"data": {...}}` envelope. Test fixtures in tests/fixtures/ store the full
            envelope as returned by `gh`, so tests must unwrap before calling normalize.
            Pure. Maps the payload to normalized events sorted by (at, id). Synthesizes the
            `created` event, which GitHub does not emit. Skips PENDING reviews.

    derive.py
        derive(events: list[Event], snapshot: Snapshot, cfg: Config, now: datetime)
            -> tuple[Milestones, Metrics]
            Pure: no I/O, no network, no datetime.now() — `now` is injected.

    store.py
        record_path(ref: PRRef) -> pathlib.Path
        load_record(ref: PRRef) -> PRRecord | None
        save_record(record: PRRecord) -> None
        merge_events(stored: list[Event], fetched: list[Event]) -> list[Event]
            Union by Event.id, preferring the STORED copy on conflict (a re-fetch reports a
            dismissed approval as DISMISSED and loses the original approval timestamp).

    report.py
        build_index(records: list[PRRecord]) -> list[dict]
        write_csv(rows: list[dict], path: pathlib.Path) -> None
        render_markdown(records: list[PRRecord], cfg: Config, now: datetime) -> str
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

SCHEMA_VERSION = 1

EventType = Literal[
    "created",
    "ready_for_review",
    "convert_to_draft",
    "review",
    "review_requested",
    "review_request_removed",
    "review_dismissed",
    "merged",
    "closed",
    "reopened",
]

# GitHub's PullRequestReviewState. PENDING is never stored: it is an unsubmitted draft
# review, visible only to its author.
ReviewState = Literal["COMMENTED", "APPROVED", "CHANGES_REQUESTED", "DISMISSED"]

ReviewerClass = Literal["internal", "external", "other"]

# Whether the two-stage review pipeline was actually followed: internal reviewers (our own
# team) sign off first, then the PR is handed to the external repo maintainers. This is a
# *fact about the sequence*, not a judgement — `external_only` and `external_first` are
# normal on repos we don't control, and are worth seeing rather than hiding.
#
#   none           no approval from either class yet
#   internal_only  our team approved; still waiting on a maintainer
#   external_only  a maintainer approved with no internal sign-off — pipeline bypassed
#   internal_first both approved, in the intended order
#   external_first both approved, but the maintainer got there first
ApprovalPath = Literal["none", "internal_only", "external_only", "internal_first", "external_first"]

PRState = Literal["OPEN", "CLOSED", "MERGED"]

Tracking = Literal["active", "final"]

# Timeline __typename -> normalized EventType. The two draft transitions are what 6.1's
# parity inference counts.
TYPENAME_TO_EVENT: dict[str, EventType] = {
    "ReadyForReviewEvent": "ready_for_review",
    "ConvertToDraftEvent": "convert_to_draft",
    "PullRequestReview": "review",
    "ReviewRequestedEvent": "review_requested",
    "ReviewRequestRemovedEvent": "review_request_removed",
    "ReviewDismissedEvent": "review_dismissed",
    "MergedEvent": "merged",
    "ClosedEvent": "closed",
    "ReopenedEvent": "reopened",
}

DRAFT_TRANSITIONS: frozenset[str] = frozenset({"ready_for_review", "convert_to_draft"})


@dataclass(frozen=True)
class PRRef:
    """A watchlist entry, parsed from a PR URL."""

    owner: str
    repo: str
    number: int

    @property
    def key(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"

    @property
    def slug(self) -> str:
        """Filename stem: `owner__repo__number`."""
        return f"{self.owner}__{self.repo}__{self.number}"


@dataclass
class Config:
    internal_reviewers: frozenset[str] = frozenset()
    external_reviewers: frozenset[str] = frozenset()
    stale_review_hours: int = 48
    stale_merge_hours: int = 72
    percentiles: tuple[int, ...] = (50, 90)

    def classify(self, login: str | None) -> ReviewerClass:
        """Case-insensitive, `@`-stripped. Unknown logins are `other` and satisfy NEITHER
        the internal nor the external approval milestone."""
        if not login:
            return "other"
        norm = login.lstrip("@").lower()
        if norm in self.internal_reviewers:
            return "internal"
        if norm in self.external_reviewers:
            return "external"
        return "other"


@dataclass
class Event:
    id: str
    type: EventType
    at: datetime
    actor: str | None = None
    review_state: ReviewState | None = None
    # Recomputed on every read, so editing the reviewer lists takes effect immediately.
    reviewer_class: ReviewerClass | None = None


@dataclass
class PRMeta:
    title: str
    author: str | None
    base_ref: str


@dataclass
class Snapshot:
    state: PRState
    is_draft: bool
    additions: int
    deletions: int
    changed_files: int
    fetched_at: datetime


@dataclass
class Milestones:
    created_at: datetime
    ready_at: datetime | None = None
    draft_at_creation: bool = False
    # end is None while the PR is currently a draft; closed at `now` when measuring.
    draft_intervals: list[tuple[datetime, datetime | None]] = field(default_factory=list)
    first_review_at: datetime | None = None
    # Per-class first review. `first_review_at` stays class-agnostic (it includes `other`);
    # these two separate "how fast does our team pick it up" — which we control — from "how
    # fast do the maintainers respond", which we do not.
    first_internal_review_at: datetime | None = None
    first_external_review_at: datetime | None = None
    first_changes_requested_at: datetime | None = None
    internal_approved_at: datetime | None = None
    external_approved_at: datetime | None = None
    other_approved_at: datetime | None = None
    approval_path: ApprovalPath = "none"
    merged_at: datetime | None = None
    closed_at: datetime | None = None


@dataclass
class Metrics:
    """`None` means the milestone was never reached. Never collapse that into 0.0."""

    hours_draft_total: float | None = None
    hours_created_to_ready: float | None = None
    ready_hours_to_first_review: float | None = None
    ready_hours_to_internal_review: float | None = None
    ready_hours_to_external_review: float | None = None
    ready_hours_to_internal_approval: float | None = None
    ready_hours_to_external_approval: float | None = None
    # Handoff latency: internal sign-off -> maintainer approval, the stretch we wait through
    # but do not control. Only meaningful when approval_path == "internal_first"; None
    # otherwise, because a clamped 0.0 would read as "instant handoff" when what actually
    # happened is that the pipeline was bypassed or ran backwards.
    ready_hours_internal_to_external_approval: float | None = None
    wall_hours_to_merge: float | None = None
    changes_requested_count: int = 0
    review_rounds: int = 0
    distinct_reviewers: int = 0
    open_hours: float | None = None


@dataclass
class PRRecord:
    ref: PRRef
    meta: PRMeta
    snapshot: Snapshot
    events: list[Event]
    milestones: Milestones
    metrics: Metrics
    in_watchlist: bool = True
    tracking: Tracking = "active"
    last_error: str | None = None
    history_runs: int = 0
    schema_version: int = SCHEMA_VERSION
