"""Turn a raw `pullRequest` payload into `(PRMeta, Snapshot, list[Event])`. Pure.

Public surface: `normalize(payload, ref, *, fetched_at=None)`.

`payload` is the UNWRAPPED `data.repository.pullRequest` object, i.e. exactly what
`github.fetch_pull_request` returns. Fixtures in tests/fixtures/ store the full `gh`
envelope, so tests unwrap first.

Three id schemes live here, all deterministic so that `store.merge_events` can union event
lists across runs by id:

1. Real events keep GitHub's node `id`.
2. The synthesized `created` event — GitHub emits no timeline item for PR creation — gets
   `created:<pullRequest.id>`, falling back to `created:<owner/repo#number>` when the node
   id is absent. The PR node id is immutable, so this survives renames.
3. An event that arrives without an `id` (GitHub has shipped timeline items with null ids)
   gets `sha1("<type>|<formatted at>|<actor or empty>")`.hexdigest(). Same event, same id,
   run after run.

Events are sorted by `(at, id)`. The id tiebreak is not cosmetic: CODEOWNERS fires three
`ReviewRequestedEvent`s inside the same second, and an unstable order there would make the
stored event list churn on every fetch.

`reviewer_class` is deliberately left `None` — `derive.py` recomputes it on every read so
that editing config/*-reviewers.txt takes effect without a re-fetch.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, cast

from pr_tracker.contracts import (
    TYPENAME_TO_EVENT,
    Event,
    EventType,
    PRMeta,
    PRRef,
    PRState,
    ReviewState,
    Snapshot,
)
from pr_tracker.timeutil import format_ts, parse_ts

# Unsubmitted draft review, visible only to its author (design 4.1).
_SKIPPED_REVIEW_STATE = "PENDING"

_VALID_STATES: frozenset[str] = frozenset({"OPEN", "CLOSED", "MERGED"})


def normalize(
    payload: dict,
    ref: PRRef,
    *,
    fetched_at: datetime | None = None,
) -> tuple[PRMeta, Snapshot, list[Event]]:
    """Normalize one PR payload. No I/O, no clock reads.

    `fetched_at` is injected rather than sampled from `datetime.now()`, which would make
    this function impure and its output untestable. Callers (cli.py) should pass the run's
    single `now`. When omitted, the newest timestamp present in the payload is used, so the
    result stays deterministic instead of silently depending on wall-clock time.
    """
    meta = _meta(payload)
    events = _events(payload, ref)
    snapshot = _snapshot(payload, fetched_at=fetched_at, events=events)
    return meta, snapshot, events


def _meta(payload: dict) -> PRMeta:
    return PRMeta(
        title=str(payload.get("title") or ""),
        author=_login(payload.get("author")),
        base_ref=str(payload.get("baseRefName") or ""),
    )


def _snapshot(
    payload: dict,
    *,
    fetched_at: datetime | None,
    events: list[Event],
) -> Snapshot:
    raw_state = str(payload.get("state") or "OPEN").upper()
    state = cast("PRState", raw_state if raw_state in _VALID_STATES else "OPEN")
    return Snapshot(
        state=state,
        is_draft=bool(payload.get("isDraft")),
        additions=_int(payload.get("additions")),
        deletions=_int(payload.get("deletions")),
        changed_files=_int(payload.get("changedFiles")),
        fetched_at=fetched_at if fetched_at is not None else _latest_known(payload, events),
    )


def _latest_known(payload: dict, events: list[Event]) -> datetime:
    """Deterministic stand-in for an uninjected `fetched_at`: the newest time we can see."""
    candidates = [event.at for event in events]
    for key in ("mergedAt", "closedAt", "createdAt"):
        parsed = _maybe_ts(payload.get(key))
        if parsed is not None:
            candidates.append(parsed)
    if not candidates:
        raise ValueError("cannot infer fetched_at: payload has no timestamps; pass it explicitly")
    return max(candidates)


def _events(payload: dict, ref: PRRef) -> list[Event]:
    events: list[Event] = []

    created_at = _maybe_ts(payload.get("createdAt"))
    if created_at is not None:
        events.append(
            Event(
                id=_created_id(payload, ref),
                type="created",
                at=created_at,
                actor=_login(payload.get("author")),
            )
        )

    timeline = payload.get("timelineItems") or {}
    nodes = timeline.get("nodes") if isinstance(timeline, dict) else None
    for node in nodes or []:
        event = _event_from_node(node)
        if event is not None:
            events.append(event)

    events.sort(key=lambda event: (event.at, event.id))
    return events


def _created_id(payload: dict, ref: PRRef) -> str:
    node_id = payload.get("id")
    return f"created:{node_id}" if node_id else f"created:{ref.key}"


def _event_from_node(node: Any) -> Event | None:
    if not isinstance(node, dict):
        return None

    typename = node.get("__typename")
    event_type = TYPENAME_TO_EVENT.get(str(typename))
    if event_type is None:
        # GitHub adds timeline types over time; an unknown one is not an error.
        return None

    review_state: ReviewState | None = None
    if event_type == "review":
        raw_state = node.get("state")
        state = str(raw_state).upper() if raw_state else None
        if state == _SKIPPED_REVIEW_STATE:
            return None
        review_state = cast("ReviewState | None", state)
        at = _maybe_ts(node.get("submittedAt")) or _maybe_ts(node.get("createdAt"))
        actor = _login(node.get("author"))
    else:
        at = _maybe_ts(node.get("createdAt"))
        actor = _login(node.get("actor"))

    if at is None:
        # Without a timestamp the event cannot be ordered or measured; dropping it is
        # better than inventing a time that derivation would treat as real.
        return None

    node_id = node.get("id")
    event_id = str(node_id) if node_id else _synthetic_id(event_type, at, actor)

    return Event(
        id=event_id,
        type=event_type,
        at=at,
        actor=actor,
        review_state=review_state,
        reviewer_class=None,  # derive.py owns this
    )


def _synthetic_id(event_type: EventType, at: datetime, actor: str | None) -> str:
    material = f"{event_type}|{format_ts(at)}|{actor or ''}"
    return hashlib.sha1(material.encode("utf-8"), usedforsecurity=False).hexdigest()


def _login(container: Any) -> str | None:
    """`actor`/`author` is null for deleted users and for some app-generated events."""
    if not isinstance(container, dict):
        return None
    login = container.get("login")
    return str(login) if login else None


def _maybe_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return parse_ts(value)
    except ValueError:
        return None


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
