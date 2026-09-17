"""Storage tests. The JSON boundary is where accumulated history can be silently lost, so
these lean on round-trips, on the stored-wins merge rule, and on loud failure for anything
we cannot interpret.
"""

import json
from datetime import UTC, datetime

import pytest

from pr_tracker import store
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


def t(day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, second, tzinfo=UTC)


REF = PRRef(owner="octo", repo="api", number=123)


@pytest.fixture(autouse=True)
def _isolated_data_root(tmp_path, monkeypatch):
    """Never let a test touch the real `data/`."""
    monkeypatch.setattr(store, "DATA_ROOT", tmp_path / "data")
    return tmp_path / "data"


def make_record(**overrides) -> PRRecord:
    base = {
        "ref": REF,
        "meta": PRMeta(title="Add retry to publisher", author="alice", base_ref="main"),
        "snapshot": Snapshot(
            state="OPEN",
            is_draft=False,
            additions=210,
            deletions=34,
            changed_files=7,
            fetched_at=t(17, 16, 5, 11),
        ),
        "events": [
            Event(id="PR_1", type="created", at=t(10, 8), actor="alice"),
            Event(id="RFR_1", type="ready_for_review", at=t(11, 9, 15), actor="alice"),
            Event(
                id="PRR_1",
                type="review",
                at=t(11, 14, 2),
                actor="bob",
                review_state="CHANGES_REQUESTED",
                reviewer_class="internal",
            ),
        ],
        "milestones": Milestones(
            created_at=t(10, 8),
            ready_at=t(11, 9, 15),
            draft_at_creation=True,
            draft_intervals=[(t(10, 8), t(11, 9, 15))],
            first_review_at=t(11, 14, 2),
            first_changes_requested_at=t(11, 14, 2),
            internal_approved_at=None,
            external_approved_at=None,
            other_approved_at=None,
            merged_at=None,
            closed_at=None,
        ),
        "metrics": Metrics(
            hours_draft_total=25.25,
            hours_created_to_ready=25.25,
            ready_hours_to_first_review=4.78,
            ready_hours_to_internal_approval=None,
            ready_hours_to_external_approval=None,
            wall_hours_to_merge=None,
            changes_requested_count=1,
            review_rounds=1,
            distinct_reviewers=1,
            open_hours=200.1,
        ),
        "history_runs": 14,
    }
    base.update(overrides)
    return PRRecord(**base)


class TestRecordPath:
    def test_slug_layout(self):
        path = store.record_path(REF)
        assert path.name == "octo__api__123.json"
        assert path.parent.name == "prs"

    def test_explicit_root_argument_wins(self, tmp_path):
        assert store.record_path(REF, tmp_path).parent == tmp_path / "prs"


class TestRoundTrip:
    def test_full_record_survives(self):
        record = make_record()
        store.save_record(record)
        loaded = store.load_record(REF)

        assert loaded == record

    def test_datetimes_come_back_aware_utc(self):
        store.save_record(make_record())
        loaded = store.load_record(REF)

        assert loaded.snapshot.fetched_at == t(17, 16, 5, 11)
        assert loaded.snapshot.fetched_at.tzinfo is not None
        assert loaded.events[0].at == t(10, 8)
        assert loaded.milestones.ready_at == t(11, 9, 15)

    def test_open_ended_draft_interval_round_trips(self):
        """A PR currently in draft has an interval whose end is null on disk."""
        record = make_record(
            snapshot=Snapshot(
                state="OPEN",
                is_draft=True,
                additions=1,
                deletions=0,
                changed_files=1,
                fetched_at=t(17),
            ),
            milestones=Milestones(
                created_at=t(10, 8),
                ready_at=t(11, 9, 15),
                draft_at_creation=False,
                draft_intervals=[(t(11, 9, 15), t(12)), (t(15), None)],
            ),
        )
        store.save_record(record)

        on_disk = json.loads(store.record_path(REF).read_text(encoding="utf-8"))
        assert on_disk["milestones"]["draft_intervals"] == [
            ["2026-09-11T09:15:00Z", "2026-09-12T00:00:00Z"],
            ["2026-09-15T00:00:00Z", None],
        ]

        loaded = store.load_record(REF)
        assert loaded.milestones.draft_intervals == [(t(11, 9, 15), t(12)), (t(15), None)]

    def test_none_metrics_stay_none(self):
        store.save_record(make_record())
        loaded = store.load_record(REF)
        assert loaded.metrics.wall_hours_to_merge is None
        assert loaded.metrics.ready_hours_to_internal_approval is None

    def test_dict_helpers_are_exported_and_symmetric(self):
        record = make_record()
        assert store.record_from_dict(store.record_to_dict(record)) == record

    def test_derived_fields_are_written_for_downstream_consumers(self):
        payload = store.record_to_dict(make_record())
        assert payload["key"] == "octo/api#123"
        assert payload["url"] == "https://github.com/octo/api/pull/123"
        assert payload["schema_version"] == SCHEMA_VERSION


class TestWriteFormat:
    def test_deterministic_json(self):
        store.save_record(make_record())
        text = store.record_path(REF).read_text(encoding="utf-8")

        assert text.endswith("\n")
        assert '\n  "author"' in text  # 2-space indent
        keys = [line.strip().split('"')[1] for line in text.splitlines() if line.startswith('  "')]
        assert keys == sorted(keys)

    def test_rewrite_is_byte_identical(self):
        record = make_record()
        store.save_record(record)
        first = store.record_path(REF).read_bytes()
        store.save_record(store.load_record(REF))
        assert store.record_path(REF).read_bytes() == first

    def test_no_temp_files_left_behind(self):
        store.save_record(make_record())
        names = sorted(p.name for p in store.prs_dir().iterdir())
        assert names == ["octo__api__123.json"]

    def test_write_creates_parent_directories(self, tmp_path):
        store.save_record(make_record(), tmp_path / "nested" / "deep")
        assert (tmp_path / "nested" / "deep" / "prs" / "octo__api__123.json").exists()


class TestLoadFailures:
    def test_missing_file_returns_none(self):
        assert store.load_record(REF) is None

    def test_corrupt_json_raises(self):
        path = store.record_path(REF)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"schema_version": 1, "owner": "octo",', encoding="utf-8")

        with pytest.raises(store.StoreError, match="not valid JSON"):
            store.load_record(REF)

    def test_truncated_but_valid_json_raises_rather_than_returning_none(self):
        store.record_path(REF).parent.mkdir(parents=True, exist_ok=True)
        store.record_path(REF).write_text('{"owner": "octo"}', encoding="utf-8")

        with pytest.raises(store.StoreError, match="missing required key"):
            store.load_record(REF)

    def test_newer_schema_version_raises(self):
        payload = store.record_to_dict(make_record())
        payload["schema_version"] = SCHEMA_VERSION + 1
        store.write_json_atomic(store.record_path(REF), payload)

        with pytest.raises(store.StoreError, match="newer than this build"):
            store.load_record(REF)

    def test_older_schema_version_is_accepted(self):
        payload = store.record_to_dict(make_record())
        payload["schema_version"] = SCHEMA_VERSION - 1
        store.write_json_atomic(store.record_path(REF), payload)

        assert store.load_record(REF).schema_version == SCHEMA_VERSION - 1

    def test_bad_timestamp_raises_with_the_field_name(self):
        payload = store.record_to_dict(make_record())
        payload["milestones"]["created_at"] = "yesterday"
        store.write_json_atomic(store.record_path(REF), payload)

        with pytest.raises(store.StoreError, match="created_at"):
            store.load_record(REF)

    def test_malformed_draft_interval_raises(self):
        payload = store.record_to_dict(make_record())
        payload["milestones"]["draft_intervals"] = [["2026-09-10T08:00:00Z"]]
        store.write_json_atomic(store.record_path(REF), payload)

        with pytest.raises(store.StoreError, match="two-element"):
            store.load_record(REF)


class TestLoadAllRecords:
    def test_empty_when_no_directory(self):
        assert store.load_all_records() == []

    def test_sorted_by_ref(self):
        store.save_record(make_record())
        other = make_record(ref=PRRef(owner="acme", repo="web", number=9))
        store.save_record(other)

        keys = [record.ref.key for record in store.load_all_records()]
        assert keys == ["acme/web#9", "octo/api#123"]


class TestMergeEvents:
    def test_stored_copy_wins_on_id_conflict(self):
        """The dismissal case: a re-fetch reports DISMISSED and loses the approval time."""
        stored = [
            Event(
                id="PRR_1",
                type="review",
                at=t(12, 10, 30),
                actor="bob",
                review_state="APPROVED",
                reviewer_class="internal",
            )
        ]
        fetched = [
            Event(
                id="PRR_1",
                type="review",
                at=t(14, 9),
                actor="bob",
                review_state="DISMISSED",
                reviewer_class="internal",
            )
        ]

        merged = store.merge_events(stored, fetched)

        assert len(merged) == 1
        assert merged[0].review_state == "APPROVED"
        assert merged[0].at == t(12, 10, 30)

    def test_unions_new_fetched_events(self):
        stored = [Event(id="A", type="created", at=t(10))]
        fetched = [
            Event(id="A", type="created", at=t(10)),
            Event(id="B", type="merged", at=t(12)),
        ]
        assert [event.id for event in store.merge_events(stored, fetched)] == ["A", "B"]

    def test_dedupes_within_each_side(self):
        stored = [Event(id="A", type="created", at=t(10))] * 2
        fetched = [Event(id="B", type="merged", at=t(12))] * 3
        assert [event.id for event in store.merge_events(stored, fetched)] == ["A", "B"]

    def test_sorts_by_at_then_id(self):
        stored = [
            Event(id="z", type="review", at=t(11)),
            Event(id="a", type="review", at=t(11)),
            Event(id="m", type="created", at=t(10)),
        ]
        merged = store.merge_events(stored, [])
        assert [event.id for event in merged] == ["m", "a", "z"]

    def test_empty_sides(self):
        assert store.merge_events([], []) == []
        only_fetched = store.merge_events([], [Event(id="A", type="created", at=t(10))])
        assert [event.id for event in only_fetched] == ["A"]

    def test_inputs_are_not_mutated(self):
        stored = [Event(id="A", type="created", at=t(10))]
        fetched = [Event(id="B", type="merged", at=t(12))]
        store.merge_events(stored, fetched)
        assert len(stored) == 1
        assert len(fetched) == 1

    def test_merged_events_round_trip_through_storage(self):
        merged = store.merge_events(
            [Event(id="A", type="created", at=t(10), actor="alice")],
            [Event(id="B", type="merged", at=t(12), actor="carol")],
        )
        store.save_record(make_record(events=merged))
        assert store.load_record(REF).events == merged
