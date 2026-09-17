"""Rendering: the flat rollup (`index.json`, `metrics.csv`) and the markdown report.

See docs/design.md §9. Three rules drive everything here:

* Medians and percentiles, never means — one stalled PR wrecks a mean.
* A `None` metric means "the milestone was never reached" and must never render as `0`.
  It renders as `—`; a value whose clock is still running renders as `⏳ 3d`.
* Output is a pure function of (records, cfg, now), so the report can be snapshot-tested
  and so a re-run with unchanged data produces a byte-identical file (no commit churn).

The rollup is deliberately flat and stable: a future GitHub Pages dashboard reads
`index.json` directly, so it and the markdown renderer cannot drift apart.
"""

from __future__ import annotations

import csv
import statistics
from datetime import datetime
from pathlib import Path

from pr_tracker.contracts import ApprovalPath, Config, PRRecord
from pr_tracker.derive import approval_path_of
from pr_tracker.timeutil import format_ts, hours_between, hours_excluding

NOT_REACHED = "—"
IN_FLIGHT = "⏳"

# Explicit and fixed: the CSV must not reorder columns because a dict happened to change
# insertion order. Append new columns at the end.
CSV_COLUMNS: tuple[str, ...] = (
    "key",
    "owner",
    "repo",
    "number",
    "url",
    "title",
    "author",
    "base_ref",
    "in_watchlist",
    "tracking",
    "state",
    "is_draft",
    "additions",
    "deletions",
    "changed_files",
    "fetched_at",
    "created_at",
    "ready_at",
    "draft_at_creation",
    "first_review_at",
    "first_changes_requested_at",
    "internal_approved_at",
    "external_approved_at",
    "other_approved_at",
    "merged_at",
    "closed_at",
    "hours_draft_total",
    "hours_created_to_ready",
    "ready_hours_to_first_review",
    "ready_hours_to_internal_approval",
    "ready_hours_to_external_approval",
    "wall_hours_to_merge",
    "changes_requested_count",
    "review_rounds",
    "distinct_reviewers",
    "open_hours",
    "event_count",
    "history_runs",
    "last_error",
    "schema_version",
    # Appended (never inserted) when the two-stage pipeline model was added — existing
    # consumers keep their column positions.
    "first_internal_review_at",
    "first_external_review_at",
    "approval_path",
    "ready_hours_to_internal_review",
    "ready_hours_to_external_review",
    "ready_hours_internal_to_external_approval",
)

# (attribute on Metrics, column heading). Order is the report's column order.
AGGREGATE_METRICS: tuple[tuple[str, str], ...] = (
    ("hours_draft_total", "Draft total"),
    ("hours_created_to_ready", "Created → ready"),
    ("ready_hours_to_first_review", "Ready → first review"),
    ("ready_hours_to_internal_review", "Ready → internal review"),
    ("ready_hours_to_external_review", "Ready → external review"),
    ("ready_hours_to_internal_approval", "Ready → internal approval"),
    ("ready_hours_to_external_approval", "Ready → external approval"),
    ("ready_hours_internal_to_external_approval", "Internal → external approval (handoff)"),
    ("wall_hours_to_merge", "Created → merge (wall)"),
    ("open_hours", "Open duration"),
)


# --------------------------------------------------------------------------------------
# Rollup
# --------------------------------------------------------------------------------------


def _ts(value: datetime | None) -> str | None:
    return None if value is None else format_ts(value)


def _path_of(record: PRRecord) -> ApprovalPath:
    """The record's approval path, recomputed from its own approval timestamps.

    Not `record.milestones.approval_path`: that field is what gets *written out* for external
    consumers, but a record stored before it existed defaults to `"none"`, which would quietly
    drop the PR out of the pipeline counts and the attention list until `verify --write` ran.
    """
    return approval_path_of(
        record.milestones.internal_approved_at, record.milestones.external_approved_at
    )


def build_index(records: list[PRRecord]) -> list[dict]:
    """One flat, JSON-serializable dict per record — no nested events.

    Includes every record, watchlisted or not, with `in_watchlist` as a column: dropping a
    PR from `config/prs.txt` should not make its accumulated history vanish from the rollup.
    """
    rows: list[dict] = []
    for record in records:
        milestones = record.milestones
        metrics = record.metrics
        rows.append(
            {
                "key": record.ref.key,
                "owner": record.ref.owner,
                "repo": record.ref.repo,
                "number": record.ref.number,
                "url": record.ref.url,
                "title": record.meta.title,
                "author": record.meta.author,
                "base_ref": record.meta.base_ref,
                "in_watchlist": record.in_watchlist,
                "tracking": record.tracking,
                "state": record.snapshot.state,
                "is_draft": record.snapshot.is_draft,
                "additions": record.snapshot.additions,
                "deletions": record.snapshot.deletions,
                "changed_files": record.snapshot.changed_files,
                "fetched_at": format_ts(record.snapshot.fetched_at),
                "created_at": format_ts(milestones.created_at),
                "ready_at": _ts(milestones.ready_at),
                "draft_at_creation": milestones.draft_at_creation,
                "first_review_at": _ts(milestones.first_review_at),
                "first_internal_review_at": _ts(milestones.first_internal_review_at),
                "first_external_review_at": _ts(milestones.first_external_review_at),
                "first_changes_requested_at": _ts(milestones.first_changes_requested_at),
                "internal_approved_at": _ts(milestones.internal_approved_at),
                "external_approved_at": _ts(milestones.external_approved_at),
                "other_approved_at": _ts(milestones.other_approved_at),
                "approval_path": _path_of(record),
                "merged_at": _ts(milestones.merged_at),
                "closed_at": _ts(milestones.closed_at),
                "hours_draft_total": metrics.hours_draft_total,
                "hours_created_to_ready": metrics.hours_created_to_ready,
                "ready_hours_to_first_review": metrics.ready_hours_to_first_review,
                "ready_hours_to_internal_review": metrics.ready_hours_to_internal_review,
                "ready_hours_to_external_review": metrics.ready_hours_to_external_review,
                "ready_hours_to_internal_approval": metrics.ready_hours_to_internal_approval,
                "ready_hours_to_external_approval": metrics.ready_hours_to_external_approval,
                "ready_hours_internal_to_external_approval": (
                    metrics.ready_hours_internal_to_external_approval
                ),
                "wall_hours_to_merge": metrics.wall_hours_to_merge,
                "changes_requested_count": metrics.changes_requested_count,
                "review_rounds": metrics.review_rounds,
                "distinct_reviewers": metrics.distinct_reviewers,
                "open_hours": metrics.open_hours,
                "event_count": len(record.events),
                "history_runs": record.history_runs,
                "last_error": record.last_error,
                "schema_version": record.schema_version,
            }
        )
    rows.sort(key=lambda row: (row["owner"], row["repo"], row["number"]))
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    """Write the rollup as CSV with a fixed column order. `None` becomes an empty cell."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(CSV_COLUMNS),
            restval="",
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {key: "" if row.get(key) is None else row.get(key) for key in CSV_COLUMNS}
            )


# --------------------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------------------


def format_duration(value: float | None) -> str:
    """Hours as `2d 4h` / `20.7h` / `35m`. `None` is the un-reached milestone, never 0."""
    if value is None:
        return NOT_REACHED
    hours = max(float(value), 0.0)
    if hours >= 24:
        days = int(hours // 24)
        rest = int(round(hours - days * 24))
        if rest == 24:  # rounding pushed it over a day boundary
            days += 1
            rest = 0
        return f"{days}d" if rest == 0 else f"{days}d {rest}h"
    if hours < 1:
        return f"{int(round(hours * 60))}m"
    return f"{hours:.1f}h"


def format_metric(value: float | None, *, in_flight: bool = False) -> str:
    """A completed duration, an in-flight one (`⏳ 3d`), or `—`."""
    if value is None:
        return NOT_REACHED
    if in_flight:
        return f"{IN_FLIGHT} {format_duration(value)}"
    return format_duration(value)


def _escape(text: str | None) -> str:
    """Keep free-text cells from breaking the table."""
    if not text:
        return ""
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _percentile(values: list[float], percentile: int) -> float | None:
    """Linear-interpolated inclusive percentile. p50 is the median by construction."""
    if not values:
        return None
    ordered = sorted(values)
    if percentile == 50:
        return statistics.median(ordered)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile / 100.0
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


# --------------------------------------------------------------------------------------
# In-flight clocks
# --------------------------------------------------------------------------------------


def _ready_hours_so_far(record: PRRecord, now: datetime) -> float | None:
    """Ready hours elapsed since the PR first became ready, excluding draft time."""
    ready_at = record.milestones.ready_at
    if ready_at is None:
        return None
    return hours_excluding(ready_at, now, record.milestones.draft_intervals, now=now)


def _is_live(record: PRRecord) -> bool:
    """A clock is only still running while the PR is open."""
    return record.snapshot.state == "OPEN"


def _metric_cell(
    record: PRRecord, attribute: str, elapsed: float | None
) -> tuple[float | None, bool]:
    """(value, in_flight). An unreached milestone on an open PR still has a running clock;
    on a closed or merged PR it never will, so it stays `None` and renders as `—`."""
    value = getattr(record.metrics, attribute)
    if value is not None:
        return value, False
    if _is_live(record) and elapsed is not None:
        return elapsed, True
    return None, False


# --------------------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------------------


def _sorted_records(records: list[PRRecord]) -> list[PRRecord]:
    """Newest PR first; PR key breaks ties so the order is total and stable."""
    return sorted(
        records,
        key=lambda record: (-record.milestones.created_at.timestamp(), record.ref.key),
    )


def _header_lines(records: list[PRRecord], now: datetime) -> list[str]:
    counts = {"OPEN": 0, "MERGED": 0, "CLOSED": 0}
    drafts = 0
    for record in records:
        counts[record.snapshot.state] = counts.get(record.snapshot.state, 0) + 1
        if record.snapshot.is_draft:
            drafts += 1
    lines = [
        "# PR progress",
        "",
        f"Generated {format_ts(now)} · {len(records)} tracked PRs · "
        f"{counts['OPEN']} open ({drafts} draft) · "
        f"{counts['MERGED']} merged · {counts['CLOSED']} closed",
        "",
    ]
    problems = [record for record in records if record.last_error]
    if problems:
        lines += ["## Problems", ""]
        for record in _sorted_records(problems):
            lines.append(f"- [{record.ref.key}]({record.ref.url}): {_escape(record.last_error)}")
        lines.append("")
    return lines


def _aggregate_lines(records: list[PRRecord], cfg: Config) -> list[str]:
    percentiles = tuple(cfg.percentiles) or (50, 90)
    labels = ["median" if p == 50 else f"p{p}" for p in percentiles]

    header = ["Metric", f"n / {len(records)}", *labels, "max"]
    lines = [
        "## Aggregates",
        "",
        "Medians and percentiles over the PRs whose milestone completed; `n` is how many",
        "of the tracked PRs that is.",
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for attribute, label in AGGREGATE_METRICS:
        values = [
            float(getattr(record.metrics, attribute))
            for record in records
            if getattr(record.metrics, attribute) is not None
        ]
        cells = [label, str(len(values))]
        cells += [format_duration(_percentile(values, p)) for p in percentiles]
        cells.append(format_duration(max(values) if values else None))
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return lines


# (approval_path value, label). Order is the order they appear in the report.
_APPROVAL_PATH_LABELS: tuple[tuple[str, str], ...] = (
    ("internal_first", "Internal → external (intended order)"),
    ("external_first", "External approved before internal"),
    ("internal_only", "Internal only — awaiting a maintainer"),
    ("external_only", "External only — internal review bypassed"),
    ("none", "No approval yet"),
)


def _pipeline_lines(records: list[PRRecord]) -> list[str]:
    """How often the two-stage pipeline was actually followed.

    A process metric rather than an alert: `external_only` and `external_first` are normal on
    repos we do not control, but they are worth counting, because a `—` in the Internal column
    otherwise looks identical to "not reached yet".
    """
    counts = {value: 0 for value, _ in _APPROVAL_PATH_LABELS}
    for record in records:
        path = _path_of(record)
        counts[path] = counts.get(path, 0) + 1

    lines = [
        "## Review pipeline",
        "",
        "Internal reviewers are our own team, who sign off before the PR is handed to the",
        "external repo maintainers. This is how often that order actually held.",
        "",
        f"| Path | n / {len(records)} |",
        "| --- | --- |",
    ]
    for value, label in _APPROVAL_PATH_LABELS:
        lines.append(f"| {label} | {counts.get(value, 0)} |")
    lines.append("")
    return lines


_PR_TABLE_HEADER = (
    "PR",
    "Author",
    "State",
    "Draft",
    "→ Ready",
    "→ 1st review",
    "→ Internal ✅",
    "→ External ✅",
    "Handoff",
    "Other ✅",
    "→ Merge",
    "CR",
    "Rounds",
    "Reviewers",
)


def _pr_row(record: PRRecord, now: datetime) -> list[str]:
    milestones = record.milestones
    metrics = record.metrics
    ready_so_far = _ready_hours_so_far(record, now)

    draft_value = metrics.hours_draft_total
    draft_cell = format_metric(draft_value, in_flight=record.snapshot.is_draft)

    # Created → ready is still running only while the PR has never been readied.
    to_ready_value, to_ready_flight = _metric_cell(
        record,
        "hours_created_to_ready",
        hours_between(milestones.created_at, now) if milestones.ready_at is None else None,
    )

    first_review_value, first_review_flight = _metric_cell(
        record, "ready_hours_to_first_review", ready_so_far
    )
    internal_value, internal_flight = _metric_cell(
        record, "ready_hours_to_internal_approval", ready_so_far
    )
    external_value, external_flight = _metric_cell(
        record, "ready_hours_to_external_approval", ready_so_far
    )
    merge_value, merge_flight = _metric_cell(
        record, "wall_hours_to_merge", hours_between(milestones.created_at, now)
    )

    # The handoff clock only runs once our own team has signed off and we are waiting on a
    # maintainer. With no internal approval there is nothing to measure from, so the cell
    # stays `—` rather than borrowing the ready clock like the columns above.
    handoff_elapsed = None
    if _path_of(record) == "internal_only" and milestones.internal_approved_at is not None:
        handoff_elapsed = hours_excluding(
            milestones.internal_approved_at, now, milestones.draft_intervals, now=now
        )
    handoff_value, handoff_flight = _metric_cell(
        record, "ready_hours_internal_to_external_approval", handoff_elapsed
    )

    # Informational only: an `other` approval satisfies neither approval milestone, so it
    # gets no in-flight clock — the column just reports that it happened, and when.
    if milestones.other_approved_at is not None and milestones.ready_at is not None:
        other_cell = format_duration(
            hours_excluding(
                milestones.ready_at,
                milestones.other_approved_at,
                milestones.draft_intervals,
                now=now,
            )
        )
    elif milestones.other_approved_at is not None:
        other_cell = format_duration(
            hours_between(milestones.created_at, milestones.other_approved_at)
        )
    else:
        other_cell = NOT_REACHED

    state = record.snapshot.state
    if record.snapshot.is_draft:
        state = f"{state} (draft)"

    return [
        f"[{record.ref.key}]({record.ref.url})",
        _escape(record.meta.author) or NOT_REACHED,
        state,
        draft_cell,
        format_metric(to_ready_value, in_flight=to_ready_flight),
        format_metric(first_review_value, in_flight=first_review_flight),
        format_metric(internal_value, in_flight=internal_flight),
        format_metric(external_value, in_flight=external_flight),
        format_metric(handoff_value, in_flight=handoff_flight),
        other_cell,
        format_metric(merge_value, in_flight=merge_flight),
        str(metrics.changes_requested_count),
        str(metrics.review_rounds),
        str(metrics.distinct_reviewers),
    ]


def _pr_table_lines(records: list[PRRecord], now: datetime) -> list[str]:
    lines = [
        "## Pull requests",
        "",
        "| " + " | ".join(_PR_TABLE_HEADER) + " |",
        "| " + " | ".join(["---"] * len(_PR_TABLE_HEADER)) + " |",
    ]
    for record in records:
        lines.append("| " + " | ".join(_pr_row(record, now)) + " |")
    lines += [
        "",
        f"`{NOT_REACHED}` = milestone not reached · `{IN_FLIGHT}` = still in flight · "
        "durations are ready hours (draft time excluded) except `→ Merge`, which is wall clock.",
        "",
        "`Handoff` is internal sign-off → maintainer approval. It is blank unless our team "
        "approved first: a maintainer who approved without internal review never had a handoff "
        "to wait for.",
        "",
    ]
    return lines


def _last_changes_requested_without_followup(record: PRRecord) -> datetime | None:
    """The timestamp of a CHANGES_REQUESTED review that no later review answered."""
    last_cr: datetime | None = None
    last_review: datetime | None = None
    for event in record.events:
        if event.type != "review":
            continue
        if last_review is None or event.at > last_review:
            last_review = event.at
        if event.review_state == "CHANGES_REQUESTED" and (last_cr is None or event.at > last_cr):
            last_cr = event.at
    if last_cr is None:
        return None
    if last_review is not None and last_review > last_cr:
        return None
    return last_cr


def _attention_lines(records: list[PRRecord], cfg: Config, now: datetime) -> list[str]:
    stale_review: list[str] = []
    awaiting_maintainer: list[str] = []
    stale_merge: list[str] = []
    unanswered: list[str] = []

    for record in records:
        if not _is_live(record):
            continue
        link = f"[{record.ref.key}]({record.ref.url})"
        milestones = record.milestones
        ready_so_far = _ready_hours_so_far(record, now)

        if (
            not record.snapshot.is_draft
            and ready_so_far is not None
            and milestones.first_review_at is None
            and ready_so_far > cfg.stale_review_hours
        ):
            stale_review.append(f"- {link} — ready {format_duration(ready_so_far)}, no review yet")

        # Waiting on the other side of the pipeline: our team has approved, no maintainer has.
        # This is the bucket to nudge upstream about; it is not a merge problem.
        if _path_of(record) == "internal_only" and milestones.internal_approved_at:
            since = hours_excluding(
                milestones.internal_approved_at, now, milestones.draft_intervals, now=now
            )
            if since > cfg.stale_review_hours:
                awaiting_maintainer.append(
                    f"- {link} — internally approved {format_duration(since)} ago, "
                    "no maintainer approval"
                )

        # "Approved and unmerged" means the pipeline is *complete* — both sides signed off and
        # the PR still is not in. A PR with only one class approved is mid-pipeline, not stuck
        # at the merge step, and belongs in one of the buckets above instead.
        if (
            _path_of(record) in ("internal_first", "external_first")
            and milestones.merged_at is None
        ):
            since = hours_between(
                max(milestones.internal_approved_at, milestones.external_approved_at),  # type: ignore[type-var]
                now,
            )
            if since > cfg.stale_merge_hours:
                stale_merge.append(
                    f"- {link} — fully approved {format_duration(since)} ago, unmerged"
                )

        pending_cr = _last_changes_requested_without_followup(record)
        if pending_cr is not None:
            since = hours_between(pending_cr, now)
            unanswered.append(
                f"- {link} — changes requested {format_duration(since)} ago, no follow-up review"
            )

    lines = ["## Attention", ""]
    sections = (
        (f"Ready > {cfg.stale_review_hours}h with no review", stale_review),
        (
            f"Internally approved > {cfg.stale_review_hours}h, waiting on a maintainer",
            awaiting_maintainer,
        ),
        (f"Fully approved > {cfg.stale_merge_hours}h and still unmerged", stale_merge),
        ("Changes requested with no follow-up review", unanswered),
    )
    if not any(items for _, items in sections):
        lines += ["Nothing needs attention.", ""]
        return lines
    for title, items in sections:
        if not items:
            continue
        lines += [f"### {title}", "", *items, ""]
    return lines


def render_markdown(records: list[PRRecord], cfg: Config, now: datetime) -> str:
    """The full `reports/pr-progress.md` body. Deterministic for fixed inputs and `now`."""
    watched = _sorted_records([record for record in records if record.in_watchlist])
    lines = [
        *_header_lines(watched, now),
        *_aggregate_lines(watched, cfg),
        *_pipeline_lines(watched),
        *_pr_table_lines(watched, now),
        *_attention_lines(watched, cfg, now),
    ]
    return "\n".join(lines).rstrip("\n") + "\n"
