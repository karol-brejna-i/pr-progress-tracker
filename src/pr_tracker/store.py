"""Persistence for per-PR records: the JSON boundary.

In memory every timestamp is an aware UTC `datetime`; on disk every timestamp is an
ISO-8601 string ending in `Z`. This module is the *only* place that conversion happens
(via `timeutil.parse_ts` / `timeutil.format_ts`), so no other module has to know or care
about the wire format. See docs/design.md §5.

Two properties matter more than they look:

* Writes are deterministic (2-space indent, sorted keys, trailing newline) because every
  run commits these files — unstable key ordering would produce enormous meaningless diffs.
* Writes are atomic (temp file in the same directory, then `os.replace`) because an
  interrupted run must not leave a truncated record behind. The record is the accumulated
  history; it cannot be re-derived from GitHub once the timeline has been rewritten.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from pr_tracker.contracts import (
    SCHEMA_VERSION,
    Event,
    Metrics,
    Milestones,
    PRMeta,
    PRRecord,
    PRRef,
    Snapshot,
)
from pr_tracker.timeutil import Interval, format_ts, parse_ts

# Module-level so tests can monkeypatch it instead of writing into the real `data/`.
# Every path helper reads it at call time rather than caching a derived directory.
DATA_ROOT = Path("data")

PRS_SUBDIR = "prs"


class StoreError(Exception):
    """A stored record could not be read or is not something we are allowed to interpret.

    Deliberately loud: quietly discarding accumulated history is the worst outcome here, so
    a corrupt or future-schema file raises rather than degrading to "no record yet".
    """


def data_root(root: Path | None = None) -> Path:
    return Path(root) if root is not None else DATA_ROOT


def prs_dir(root: Path | None = None) -> Path:
    return data_root(root) / PRS_SUBDIR


def record_path(ref: PRRef, root: Path | None = None) -> Path:
    """`data/prs/{owner}__{repo}__{number}.json`."""
    return prs_dir(root) / f"{ref.slug}.json"


# --------------------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------------------


def _ts(value: datetime | None) -> str | None:
    return None if value is None else format_ts(value)


def _dt(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise StoreError(f"{field}: expected an ISO-8601 timestamp string, got {value!r}")
    try:
        return parse_ts(value)
    except ValueError as exc:
        raise StoreError(f"{field}: not a valid ISO-8601 timestamp: {value!r} ({exc})") from exc


def _opt_dt(value: Any, field: str) -> datetime | None:
    return None if value is None else _dt(value, field)


def _require(payload: dict, key: str, field: str) -> Any:
    if key not in payload:
        raise StoreError(f"{field}: missing required key {key!r}")
    return payload[key]


def _section(payload: dict, key: str, field: str) -> dict:
    section = _require(payload, key, field)
    if not isinstance(section, dict):
        raise StoreError(f"{field}.{key}: expected an object, got {type(section).__name__}")
    return section


def event_to_dict(event: Event) -> dict:
    out: dict[str, Any] = {
        "id": event.id,
        "type": event.type,
        "at": format_ts(event.at),
    }
    # Omit empty optionals rather than writing a wall of nulls into every event.
    if event.actor is not None:
        out["actor"] = event.actor
    if event.review_state is not None:
        out["review_state"] = event.review_state
    if event.reviewer_class is not None:
        out["reviewer_class"] = event.reviewer_class
    return out


def event_from_dict(payload: dict, field: str = "events[]") -> Event:
    if not isinstance(payload, dict):
        raise StoreError(f"{field}: expected an object, got {type(payload).__name__}")
    return Event(
        id=str(_require(payload, "id", field)),
        type=_require(payload, "type", field),
        at=_dt(_require(payload, "at", field), f"{field}.at"),
        actor=payload.get("actor"),
        review_state=payload.get("review_state"),
        reviewer_class=payload.get("reviewer_class"),
    )


def _intervals_to_list(intervals: list[Interval]) -> list[list[str | None]]:
    """Two-element lists; the second element is null while the PR is currently a draft."""
    return [[format_ts(start), _ts(end)] for start, end in intervals]


def _intervals_from_list(raw: Any, field: str) -> list[Interval]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise StoreError(f"{field}: expected a list of [start, end] pairs")
    intervals: list[Interval] = []
    for index, item in enumerate(raw):
        where = f"{field}[{index}]"
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise StoreError(f"{where}: expected a two-element [start, end] pair, got {item!r}")
        intervals.append((_dt(item[0], f"{where}[0]"), _opt_dt(item[1], f"{where}[1]")))
    return intervals


def record_to_dict(record: PRRecord) -> dict:
    """Flatten a record to its on-disk JSON shape (design §5.2)."""
    ref = record.ref
    return {
        "schema_version": record.schema_version,
        "key": ref.key,
        "owner": ref.owner,
        "repo": ref.repo,
        "number": ref.number,
        "url": ref.url,
        "title": record.meta.title,
        "author": record.meta.author,
        "base_ref": record.meta.base_ref,
        "in_watchlist": record.in_watchlist,
        "tracking": record.tracking,
        "snapshot": {
            "state": record.snapshot.state,
            "is_draft": record.snapshot.is_draft,
            "additions": record.snapshot.additions,
            "deletions": record.snapshot.deletions,
            "changed_files": record.snapshot.changed_files,
            "fetched_at": format_ts(record.snapshot.fetched_at),
        },
        "events": [event_to_dict(event) for event in record.events],
        "milestones": {
            "created_at": format_ts(record.milestones.created_at),
            "ready_at": _ts(record.milestones.ready_at),
            "draft_at_creation": record.milestones.draft_at_creation,
            "draft_intervals": _intervals_to_list(record.milestones.draft_intervals),
            "first_review_at": _ts(record.milestones.first_review_at),
            "first_changes_requested_at": _ts(record.milestones.first_changes_requested_at),
            "internal_approved_at": _ts(record.milestones.internal_approved_at),
            "external_approved_at": _ts(record.milestones.external_approved_at),
            "other_approved_at": _ts(record.milestones.other_approved_at),
            "merged_at": _ts(record.milestones.merged_at),
            "closed_at": _ts(record.milestones.closed_at),
        },
        "metrics": {
            "hours_draft_total": record.metrics.hours_draft_total,
            "hours_created_to_ready": record.metrics.hours_created_to_ready,
            "ready_hours_to_first_review": record.metrics.ready_hours_to_first_review,
            "ready_hours_to_internal_approval": record.metrics.ready_hours_to_internal_approval,
            "ready_hours_to_external_approval": record.metrics.ready_hours_to_external_approval,
            "wall_hours_to_merge": record.metrics.wall_hours_to_merge,
            "changes_requested_count": record.metrics.changes_requested_count,
            "review_rounds": record.metrics.review_rounds,
            "distinct_reviewers": record.metrics.distinct_reviewers,
            "open_hours": record.metrics.open_hours,
        },
        "last_error": record.last_error,
        "history_runs": record.history_runs,
    }


def record_from_dict(payload: dict, source: str = "<record>") -> PRRecord:
    """Rebuild a record from its on-disk JSON shape. Raises StoreError on anything odd."""
    if not isinstance(payload, dict):
        raise StoreError(f"{source}: expected a JSON object, got {type(payload).__name__}")

    version = payload.get("schema_version", SCHEMA_VERSION)
    if not isinstance(version, int) or isinstance(version, bool):
        raise StoreError(f"{source}: schema_version must be an integer, got {version!r}")
    if version > SCHEMA_VERSION:
        raise StoreError(
            f"{source}: schema_version {version} is newer than this build understands "
            f"({SCHEMA_VERSION}); refusing to guess. Upgrade pr_tracker."
        )

    ref = PRRef(
        owner=str(_require(payload, "owner", source)),
        repo=str(_require(payload, "repo", source)),
        number=int(_require(payload, "number", source)),
    )

    snapshot_raw = _section(payload, "snapshot", source)
    milestones_raw = _section(payload, "milestones", source)
    metrics_raw = payload.get("metrics") or {}
    if not isinstance(metrics_raw, dict):
        raise StoreError(f"{source}.metrics: expected an object")

    events_raw = payload.get("events", [])
    if not isinstance(events_raw, list):
        raise StoreError(f"{source}.events: expected a list")

    return PRRecord(
        ref=ref,
        meta=PRMeta(
            title=str(payload.get("title") or ""),
            author=payload.get("author"),
            base_ref=str(payload.get("base_ref") or ""),
        ),
        snapshot=Snapshot(
            state=_require(snapshot_raw, "state", f"{source}.snapshot"),
            is_draft=bool(snapshot_raw.get("is_draft", False)),
            additions=int(snapshot_raw.get("additions", 0)),
            deletions=int(snapshot_raw.get("deletions", 0)),
            changed_files=int(snapshot_raw.get("changed_files", 0)),
            fetched_at=_dt(
                _require(snapshot_raw, "fetched_at", f"{source}.snapshot"),
                f"{source}.snapshot.fetched_at",
            ),
        ),
        events=[
            event_from_dict(item, f"{source}.events[{i}]") for i, item in enumerate(events_raw)
        ],
        milestones=Milestones(
            created_at=_dt(
                _require(milestones_raw, "created_at", f"{source}.milestones"),
                f"{source}.milestones.created_at",
            ),
            ready_at=_opt_dt(milestones_raw.get("ready_at"), f"{source}.milestones.ready_at"),
            draft_at_creation=bool(milestones_raw.get("draft_at_creation", False)),
            draft_intervals=_intervals_from_list(
                milestones_raw.get("draft_intervals"), f"{source}.milestones.draft_intervals"
            ),
            first_review_at=_opt_dt(
                milestones_raw.get("first_review_at"), f"{source}.milestones.first_review_at"
            ),
            first_changes_requested_at=_opt_dt(
                milestones_raw.get("first_changes_requested_at"),
                f"{source}.milestones.first_changes_requested_at",
            ),
            internal_approved_at=_opt_dt(
                milestones_raw.get("internal_approved_at"),
                f"{source}.milestones.internal_approved_at",
            ),
            external_approved_at=_opt_dt(
                milestones_raw.get("external_approved_at"),
                f"{source}.milestones.external_approved_at",
            ),
            other_approved_at=_opt_dt(
                milestones_raw.get("other_approved_at"), f"{source}.milestones.other_approved_at"
            ),
            merged_at=_opt_dt(milestones_raw.get("merged_at"), f"{source}.milestones.merged_at"),
            closed_at=_opt_dt(milestones_raw.get("closed_at"), f"{source}.milestones.closed_at"),
        ),
        metrics=Metrics(
            hours_draft_total=metrics_raw.get("hours_draft_total"),
            hours_created_to_ready=metrics_raw.get("hours_created_to_ready"),
            ready_hours_to_first_review=metrics_raw.get("ready_hours_to_first_review"),
            ready_hours_to_internal_approval=metrics_raw.get("ready_hours_to_internal_approval"),
            ready_hours_to_external_approval=metrics_raw.get("ready_hours_to_external_approval"),
            wall_hours_to_merge=metrics_raw.get("wall_hours_to_merge"),
            changes_requested_count=int(metrics_raw.get("changes_requested_count") or 0),
            review_rounds=int(metrics_raw.get("review_rounds") or 0),
            distinct_reviewers=int(metrics_raw.get("distinct_reviewers") or 0),
            open_hours=metrics_raw.get("open_hours"),
        ),
        in_watchlist=bool(payload.get("in_watchlist", True)),
        tracking=payload.get("tracking", "active"),
        last_error=payload.get("last_error"),
        history_runs=int(payload.get("history_runs") or 0),
        schema_version=version,
    )


# --------------------------------------------------------------------------------------
# File I/O
# --------------------------------------------------------------------------------------


def dumps_json(payload: Any) -> str:
    """Deterministic JSON: 2-space indent, sorted keys, trailing newline."""
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_json_atomic(path: Path, payload: Any) -> None:
    """Serialize `payload` to `path` via a temp file in the same directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = dumps_json(payload)
    # Same directory so os.replace is a rename within one filesystem, hence atomic.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        # mkstemp creates 0600; these files are committed and read by everyone, so match
        # what a normal write would have produced.
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def load_record(ref: PRRef, root: Path | None = None) -> PRRecord | None:
    """Load a stored record, or None if there is no file for this PR yet."""
    path = record_path(ref, root)
    if not path.exists():
        return None
    return load_record_file(path)


def load_record_file(path: Path) -> PRRecord:
    """Load a record from an explicit path. Raises StoreError if it cannot be interpreted."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise StoreError(f"{path}: could not be read: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StoreError(f"{path}: is not valid JSON: {exc}") from exc
    return record_from_dict(payload, str(path))


def save_record(record: PRRecord, root: Path | None = None) -> None:
    """Write a record deterministically and atomically to its canonical path."""
    write_json_atomic(record_path(record.ref, root), record_to_dict(record))


def load_all_records(root: Path | None = None) -> list[PRRecord]:
    """Every stored record, sorted by PR key. Used by `report` and `verify`."""
    directory = prs_dir(root)
    if not directory.is_dir():
        return []
    records = [load_record_file(path) for path in sorted(directory.glob("*.json"))]
    records.sort(key=lambda record: (record.ref.owner, record.ref.repo, record.ref.number))
    return records


# --------------------------------------------------------------------------------------
# Event merge
# --------------------------------------------------------------------------------------


def merge_events(stored: list[Event], fetched: list[Event]) -> list[Event]:
    """Union stored and fetched events by `Event.id`, preferring the STORED copy.

    Load-bearing precedence: a re-fetch reports a dismissed approval as `DISMISSED` and
    loses the original approval timestamp, so the stored copy is the more truthful record
    of what actually happened. Only genuinely new IDs come from `fetched`.
    """
    merged: dict[str, Event] = {}
    for event in fetched:
        merged[event.id] = event
    for event in stored:
        merged[event.id] = event  # stored wins on conflict
    return sorted(merged.values(), key=lambda event: (event.at, event.id))
