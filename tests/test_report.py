"""Rendering tests. The load-bearing property is that a `None` metric — a milestone that
was never reached — never renders as `0`, in any column, in any state.
"""

import csv
from datetime import UTC, datetime

from pr_tracker import report
from pr_tracker.contracts import (
    Config,
    Event,
    Metrics,
    Milestones,
    PRMeta,
    PRRecord,
    PRRef,
    Snapshot,
)

NOW = datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)

CFG = Config(
    internal_reviewers=frozenset({"bob"}),
    external_reviewers=frozenset({"dana"}),
    stale_review_hours=48,
    stale_merge_hours=72,
    percentiles=(50, 90),
)


def t(day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


def make_record(
    number: int,
    *,
    state: str = "OPEN",
    is_draft: bool = False,
    title: str = "Some change",
    author: str | None = "alice",
    events: list[Event] | None = None,
    milestones: Milestones | None = None,
    metrics: Metrics | None = None,
    in_watchlist: bool = True,
    last_error: str | None = None,
) -> PRRecord:
    return PRRecord(
        ref=PRRef(owner="octo", repo="api", number=number),
        meta=PRMeta(title=title, author=author, base_ref="main"),
        snapshot=Snapshot(
            state=state,
            is_draft=is_draft,
            additions=10,
            deletions=2,
            changed_files=1,
            fetched_at=NOW,
        ),
        events=events if events is not None else [],
        milestones=milestones or Milestones(created_at=t(10, 8)),
        metrics=metrics or Metrics(),
        in_watchlist=in_watchlist,
        last_error=last_error,
    )


def merged_pr() -> PRRecord:
    return make_record(
        101,
        state="MERGED",
        title="Add retry to publisher",
        milestones=Milestones(
            created_at=t(10, 8),
            ready_at=t(11, 9, 15),
            draft_at_creation=True,
            draft_intervals=[(t(10, 8), t(11, 9, 15))],
            first_review_at=t(11, 14),
            first_changes_requested_at=t(11, 14),
            internal_approved_at=t(12, 10, 30),
            external_approved_at=t(12, 18),
            other_approved_at=t(12, 20),
            merged_at=t(13, 8),
        ),
        metrics=Metrics(
            hours_draft_total=25.25,
            hours_created_to_ready=25.25,
            ready_hours_to_first_review=4.78,
            ready_hours_to_internal_approval=25.25,
            ready_hours_to_external_approval=32.75,
            wall_hours_to_merge=72.0,
            changes_requested_count=1,
            review_rounds=3,
            distinct_reviewers=3,
            open_hours=72.0,
        ),
    )


def stalled_open_pr() -> PRRecord:
    """Ready 12 days ago, never reviewed: every review-side metric is None."""
    return make_record(
        102,
        title="Refactor the | pipeline",
        milestones=Milestones(created_at=t(5, 8), ready_at=t(5, 8)),
        metrics=Metrics(hours_created_to_ready=0.0, open_hours=292.0),
    )


def still_draft_pr() -> PRRecord:
    return make_record(
        103,
        is_draft=True,
        title="WIP spike",
        author=None,
        milestones=Milestones(
            created_at=t(16, 8),
            ready_at=None,
            draft_at_creation=True,
            draft_intervals=[(t(16, 8), None)],
        ),
        metrics=Metrics(hours_draft_total=28.0, open_hours=28.0),
    )


def closed_unmerged_pr() -> PRRecord:
    return make_record(
        104,
        state="CLOSED",
        title="Abandoned attempt",
        milestones=Milestones(created_at=t(8, 8), ready_at=t(8, 8), closed_at=t(9, 8)),
        metrics=Metrics(hours_created_to_ready=0.0, open_hours=24.0),
    )


def approved_unmerged_pr() -> PRRecord:
    return make_record(
        105,
        title="Approved but idle",
        milestones=Milestones(
            created_at=t(10, 8),
            ready_at=t(10, 8),
            first_review_at=t(10, 12),
            internal_approved_at=t(11, 8),
        ),
        metrics=Metrics(
            hours_created_to_ready=0.0,
            ready_hours_to_first_review=4.0,
            ready_hours_to_internal_approval=24.0,
            review_rounds=1,
            distinct_reviewers=1,
            open_hours=172.0,
        ),
    )


def changes_requested_pr() -> PRRecord:
    return make_record(
        106,
        title="Awaiting rework",
        events=[
            Event(id="PR_1", type="created", at=t(12, 8), actor="alice"),
            Event(
                id="PRR_1",
                type="review",
                at=t(13, 9),
                actor="bob",
                review_state="CHANGES_REQUESTED",
                reviewer_class="internal",
            ),
        ],
        milestones=Milestones(
            created_at=t(12, 8),
            ready_at=t(12, 8),
            first_review_at=t(13, 9),
            first_changes_requested_at=t(13, 9),
        ),
        metrics=Metrics(
            hours_created_to_ready=0.0,
            ready_hours_to_first_review=25.0,
            changes_requested_count=1,
            review_rounds=1,
            distinct_reviewers=1,
            open_hours=100.0,
        ),
    )


def all_records() -> list[PRRecord]:
    """Built fresh per test: records are mutable, so sharing one list would leak state."""
    return [
        merged_pr(),
        stalled_open_pr(),
        still_draft_pr(),
        closed_unmerged_pr(),
        approved_unmerged_pr(),
        changes_requested_pr(),
    ]


# --------------------------------------------------------------------------------------
# Duration formatting
# --------------------------------------------------------------------------------------


class TestFormatDuration:
    def test_none_is_em_dash_never_zero(self):
        assert report.format_duration(None) == report.NOT_REACHED
        assert "0" not in report.format_duration(None)

    def test_days_and_hours(self):
        assert report.format_duration(52.0) == "2d 4h"

    def test_whole_days_omit_hours(self):
        assert report.format_duration(72.0) == "3d"

    def test_sub_day_uses_one_decimal_hour(self):
        assert report.format_duration(20.66) == "20.7h"

    def test_sub_hour_uses_minutes(self):
        assert report.format_duration(0.5) == "30m"

    def test_genuine_zero_is_zero(self):
        assert report.format_duration(0.0) == "0m"

    def test_in_flight_marker(self):
        assert report.format_metric(72.0, in_flight=True) == "⏳ 3d"

    def test_in_flight_none_is_still_em_dash(self):
        assert report.format_metric(None, in_flight=True) == report.NOT_REACHED


# --------------------------------------------------------------------------------------
# Rollup
# --------------------------------------------------------------------------------------


class TestBuildIndex:
    def test_one_flat_row_per_record_with_no_nested_events(self):
        rows = report.build_index(all_records())
        assert len(rows) == 6
        for row in rows:
            assert "events" not in row
            for value in row.values():
                assert not isinstance(value, (dict, list))

    def test_rows_are_json_serializable(self):
        import json

        json.dumps(report.build_index(all_records()))

    def test_timestamps_are_iso_z_strings(self):
        row = report.build_index([merged_pr()])[0]
        assert row["created_at"] == "2026-09-10T08:00:00Z"
        assert row["merged_at"] == "2026-09-13T08:00:00Z"

    def test_unreached_milestones_are_null_not_zero(self):
        row = report.build_index([stalled_open_pr()])[0]
        assert row["first_review_at"] is None
        assert row["ready_hours_to_first_review"] is None
        assert row["wall_hours_to_merge"] is None

    def test_includes_non_watchlist_records_with_flag_column(self):
        rows = report.build_index([make_record(1, in_watchlist=False)])
        assert len(rows) == 1
        assert rows[0]["in_watchlist"] is False

    def test_sorted_by_owner_repo_number(self):
        rows = report.build_index([make_record(9), make_record(2)])
        assert [row["number"] for row in rows] == [2, 9]

    def test_row_keys_match_the_csv_columns_exactly(self):
        row = report.build_index([merged_pr()])[0]
        assert set(row) == set(report.CSV_COLUMNS)


class TestWriteCsv:
    def test_column_order_is_fixed_not_dict_order(self, tmp_path):
        path = tmp_path / "metrics.csv"
        shuffled = dict(reversed(list(report.build_index([merged_pr()])[0].items())))
        report.write_csv([shuffled], path)

        header = path.read_text(encoding="utf-8").splitlines()[0]
        assert header.split(",") == list(report.CSV_COLUMNS)

    def test_values_land_under_their_own_headers(self, tmp_path):
        path = tmp_path / "metrics.csv"
        report.write_csv(report.build_index([merged_pr()]), path)

        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 1
        assert rows[0]["key"] == "octo/api#101"
        assert rows[0]["wall_hours_to_merge"] == "72.0"

    def test_none_becomes_empty_cell_not_zero(self, tmp_path):
        path = tmp_path / "metrics.csv"
        report.write_csv(report.build_index([stalled_open_pr()]), path)

        with path.open(encoding="utf-8", newline="") as handle:
            row = next(iter(csv.DictReader(handle)))
        assert row["ready_hours_to_first_review"] == ""
        assert row["merged_at"] == ""

    def test_creates_parent_directory(self, tmp_path):
        path = tmp_path / "nested" / "metrics.csv"
        report.write_csv(report.build_index([merged_pr()]), path)
        assert path.exists()

    def test_empty_rows_still_writes_header(self, tmp_path):
        path = tmp_path / "metrics.csv"
        report.write_csv([], path)
        assert path.read_text(encoding="utf-8").strip().split(",") == list(report.CSV_COLUMNS)


# --------------------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------------------


class TestRenderMarkdown:
    def test_header_carries_generation_time_and_state_counts(self):
        out = report.render_markdown(all_records(), CFG, NOW)
        assert out.startswith("# PR progress\n")
        assert "Generated 2026-09-17T12:00:00Z" in out
        assert "6 tracked PRs" in out
        assert "4 open (1 draft)" in out
        assert "1 merged" in out
        assert "1 closed" in out

    def test_deterministic_for_the_same_inputs(self):
        first = report.render_markdown(all_records(), CFG, NOW)
        second = report.render_markdown(all_records(), CFG, NOW)
        assert first == second

    def test_sorted_by_created_at_descending(self):
        out = report.render_markdown(all_records(), CFG, NOW)
        order = [
            out.index("api#103"),  # created 09-16
            out.index("api#106"),  # created 09-12
            out.index("api#101"),  # created 09-10
            out.index("api#104"),  # created 09-08
            out.index("api#102"),  # created 09-05
        ]
        assert order == sorted(order)

    def test_excludes_records_not_in_the_watchlist(self):
        records = [merged_pr(), make_record(999, in_watchlist=False, title="Dropped")]
        out = report.render_markdown(records, CFG, NOW)
        assert "api#999" not in out
        assert "1 tracked PRs" in out

    def test_pipe_in_title_does_not_break_a_table(self):
        out = report.render_markdown([stalled_open_pr()], CFG, NOW)
        table = [line for line in out.splitlines() if line.startswith("| [octo/api#102]")]
        assert len(table) == 1
        assert table[0].count("|") == len(report._PR_TABLE_HEADER) + 1

    def test_aggregates_use_median_and_p90_not_mean(self):
        out = report.render_markdown(all_records(), CFG, NOW)
        assert "## Aggregates" in out
        assert "| median |" in out
        assert "| p90 |" in out
        assert "mean" not in out.lower()

    def test_aggregate_n_counts_only_completed_milestones(self):
        out = report.render_markdown(all_records(), CFG, NOW)
        merge_row = next(
            line for line in out.splitlines() if line.startswith("| Created → merge (wall)")
        )
        # Only the merged PR has wall_hours_to_merge.
        assert merge_row.split("|")[2].strip() == "1"

    def test_aggregate_with_no_data_shows_em_dash_not_zero(self):
        out = report.render_markdown([still_draft_pr()], CFG, NOW)
        merge_row = next(
            line for line in out.splitlines() if line.startswith("| Created → merge (wall)")
        )
        cells = [cell.strip() for cell in merge_row.split("|")[1:-1]]
        assert cells[1] == "0"  # n
        assert cells[2:] == [report.NOT_REACHED] * 3

    def test_percentile_labels_follow_config(self):
        out = report.render_markdown(all_records(), Config(percentiles=(50, 75, 99)), NOW)
        assert "| median | p75 | p99 |" in out

    def test_completed_pr_shows_real_durations(self):
        out = report.render_markdown([merged_pr()], CFG, NOW)
        row = next(line for line in out.splitlines() if "api#101" in line and line.startswith("|"))
        assert "1d 1h" in row  # 25.25h draft
        assert "4.8h" in row  # first review
        assert "3d" in row  # merge

    def test_unreached_milestone_on_closed_pr_is_em_dash_never_zero(self):
        out = report.render_markdown([closed_unmerged_pr()], CFG, NOW)
        row = next(line for line in out.splitlines() if "api#104" in line and line.startswith("|"))
        cells = [cell.strip() for cell in row.split("|")[1:-1]]
        # Draft, first review, internal, external, other, merge are all unreached.
        assert cells[3] == report.NOT_REACHED
        assert cells[5:10] == [report.NOT_REACHED] * 5
        assert report.IN_FLIGHT not in row

    def test_open_pr_with_no_review_shows_in_flight_not_zero(self):
        out = report.render_markdown([stalled_open_pr()], CFG, NOW)
        row = next(line for line in out.splitlines() if "api#102" in line and line.startswith("|"))
        cells = [cell.strip() for cell in row.split("|")[1:-1]]
        assert cells[5].startswith(report.IN_FLIGHT)  # → 1st review
        assert cells[6].startswith(report.IN_FLIGHT)  # → internal approval
        assert cells[9].startswith(report.IN_FLIGHT)  # → merge

    def test_still_draft_pr_marks_draft_in_flight_and_ready_pending(self):
        out = report.render_markdown([still_draft_pr()], CFG, NOW)
        row = next(line for line in out.splitlines() if "api#103" in line and line.startswith("|"))
        cells = [cell.strip() for cell in row.split("|")[1:-1]]
        assert cells[2] == "OPEN (draft)"
        assert cells[3] == f"{report.IN_FLIGHT} 1d 4h"  # draft total still accruing
        assert cells[4].startswith(report.IN_FLIGHT)  # created → ready
        # Never readied, so the review clock has not started at all.
        assert cells[5] == report.NOT_REACHED

    def test_other_approval_is_informational_column(self):
        out = report.render_markdown([merged_pr()], CFG, NOW)
        assert "Other ✅" in out
        row = next(line for line in out.splitlines() if "api#101" in line and line.startswith("|"))
        cells = [cell.strip() for cell in row.split("|")[1:-1]]
        assert cells[8] == "1d 11h"  # ready → other approval, draft excluded

    def test_other_approval_absent_is_em_dash(self):
        out = report.render_markdown([approved_unmerged_pr()], CFG, NOW)
        row = next(line for line in out.splitlines() if "api#105" in line and line.startswith("|"))
        cells = [cell.strip() for cell in row.split("|")[1:-1]]
        assert cells[8] == report.NOT_REACHED

    def test_missing_author_is_not_blank_zero(self):
        out = report.render_markdown([still_draft_pr()], CFG, NOW)
        row = next(line for line in out.splitlines() if "api#103" in line and line.startswith("|"))
        assert f"| {report.NOT_REACHED} |" in row

    def test_all_none_metrics_render_as_em_dash_never_zero(self):
        """Guard rail for the whole design: `None` must not become `0`.

        Closed so that no clock is running — every duration cell is a pure `None` render.
        """
        blank = make_record(301, state="CLOSED", metrics=Metrics())
        out = report.render_markdown([blank], CFG, NOW)
        row = next(line for line in out.splitlines() if line.startswith("| [octo/api#301]"))
        duration_cells = [cell.strip() for cell in row.split("|")[1:-1]][3:10]

        assert duration_cells == [report.NOT_REACHED] * 7
        for forbidden in ("0", "0m", "0.0h", "0h"):
            assert forbidden not in duration_cells

    def test_in_flight_cells_are_marked_rather_than_zeroed(self):
        """An open PR's unreached milestone shows elapsed-so-far, not 0 and not a bare number."""
        out = report.render_markdown([stalled_open_pr()], CFG, NOW)
        row = next(line for line in out.splitlines() if line.startswith("| [octo/api#102]"))
        cells = [cell.strip() for cell in row.split("|")[1:-1]]
        for index in (5, 6, 7, 9):  # review / internal / external / merge
            assert cells[index].startswith(report.IN_FLIGHT)
            assert cells[index] != f"{report.IN_FLIGHT} 0m"

    def test_genuine_zero_metric_is_distinguishable_from_none(self):
        """A PR created already-ready really did take 0 time to become ready."""
        out = report.render_markdown([stalled_open_pr()], CFG, NOW)
        row = next(line for line in out.splitlines() if line.startswith("| [octo/api#102]"))
        cells = [cell.strip() for cell in row.split("|")[1:-1]]
        assert cells[4] == "0m"  # created → ready: a real 0.0
        assert cells[3] == report.NOT_REACHED  # draft total: None

    def test_problems_section_lists_last_error(self):
        out = report.render_markdown([make_record(7, last_error="404 not found")], CFG, NOW)
        assert "## Problems" in out
        assert "404 not found" in out

    def test_no_problems_section_when_clean(self):
        assert "## Problems" not in report.render_markdown(all_records(), CFG, NOW)

    def test_ends_with_single_newline(self):
        out = report.render_markdown(all_records(), CFG, NOW)
        assert out.endswith("\n")
        assert not out.endswith("\n\n")

    def test_empty_watchlist_still_renders(self):
        out = report.render_markdown([], CFG, NOW)
        assert "0 tracked PRs" in out
        assert "Nothing needs attention." in out


class TestAttention:
    def test_ready_with_no_review_past_threshold(self):
        out = report.render_markdown([stalled_open_pr()], CFG, NOW)
        assert "Ready > 48h with no review" in out
        assert "octo/api#102" in out.split("## Attention")[1]

    def test_fresh_pr_below_threshold_is_not_flagged(self):
        fresh = make_record(
            201, milestones=Milestones(created_at=t(17, 6), ready_at=t(17, 6)), metrics=Metrics()
        )
        out = report.render_markdown([fresh], CFG, NOW)
        assert "Nothing needs attention." in out

    def test_draft_pr_is_not_flagged_for_missing_review(self):
        out = report.render_markdown([still_draft_pr()], CFG, NOW)
        assert "Nothing needs attention." in out

    def test_approved_but_unmerged_past_threshold(self):
        out = report.render_markdown([approved_unmerged_pr()], CFG, NOW)
        attention = out.split("## Attention")[1]
        assert "Approved > 72h and still unmerged" in attention
        assert "octo/api#105" in attention

    def test_merged_pr_is_never_flagged(self):
        out = report.render_markdown([merged_pr()], CFG, NOW)
        assert "Nothing needs attention." in out

    def test_closed_pr_is_never_flagged(self):
        out = report.render_markdown([closed_unmerged_pr()], CFG, NOW)
        assert "Nothing needs attention." in out

    def test_changes_requested_with_no_follow_up(self):
        out = report.render_markdown([changes_requested_pr()], CFG, NOW)
        attention = out.split("## Attention")[1]
        assert "Changes requested with no follow-up review" in attention
        assert "octo/api#106" in attention

    def test_changes_requested_answered_by_later_review_is_not_flagged(self):
        record = changes_requested_pr()
        record.events.append(
            Event(
                id="PRR_2",
                type="review",
                at=t(14, 9),
                actor="bob",
                review_state="APPROVED",
                reviewer_class="internal",
            )
        )
        record.milestones.internal_approved_at = t(14, 9)
        record.metrics.ready_hours_to_internal_approval = 49.0
        out = report.render_markdown([record], CFG, NOW)
        assert "Changes requested with no follow-up review" not in out

    def test_thresholds_come_from_config(self):
        cfg = Config(stale_review_hours=1000, stale_merge_hours=1000)
        out = report.render_markdown([stalled_open_pr(), approved_unmerged_pr()], cfg, NOW)
        assert "Nothing needs attention." in out
