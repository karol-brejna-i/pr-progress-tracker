from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pr_tracker.contracts import PRRef
from pr_tracker.normalize import normalize

FIXTURES = Path(__file__).parent / "fixtures"

FETCHED_AT = datetime(2026, 9, 17, 20, 0, 0, tzinfo=UTC)


def load_fixture(number: int) -> dict:
    """Fixtures store the full `gh` envelope; normalize takes the unwrapped pullRequest."""
    raw = json.loads((FIXTURES / f"pytorch__ao__{number}.json").read_text(encoding="utf-8"))
    return raw["data"]["repository"]["pullRequest"]


def ref(number: int) -> PRRef:
    return PRRef(owner="pytorch", repo="ao", number=number)


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class TestFixture4893:
    """MERGED, created ready, converted to draft 8s later, ready again ~23.1h after that."""

    @pytest.fixture
    def normalized(self):
        return normalize(load_fixture(4893), ref(4893), fetched_at=FETCHED_AT)

    def test_meta(self, normalized):
        meta, _, _ = normalized
        assert meta.author == "xiaowangintel"
        assert meta.base_ref == "main"
        assert meta.title.startswith("Enable  JSON serialization/deserialization")

    def test_snapshot(self, normalized):
        _, snapshot, _ = normalized
        assert snapshot.state == "MERGED"
        assert snapshot.is_draft is False
        assert (snapshot.additions, snapshot.deletions, snapshot.changed_files) == (59, 3, 2)
        assert snapshot.fetched_at == FETCHED_AT

    def test_event_sequence(self, normalized):
        _, _, events = normalized
        # 10 timeline events + the synthesized `created`.
        assert len(events) == 11
        assert [e.type for e in events] == [
            "created",
            "review_requested",
            "review_requested",
            "review_requested",
            "convert_to_draft",
            "ready_for_review",
            "review_requested",
            "review",
            "review",
            # MergedEvent and ClosedEvent share a timestamp to the second; the id tiebreak
            # puts `CE_…` before `ME_…`. Consumers must not assume merged precedes closed.
            "closed",
            "merged",
        ]

    def test_created_event_is_synthesized_from_pr_node_id(self, normalized):
        _, _, events = normalized
        created = events[0]
        assert created.id == "created:PR_kwDOKo_lrs8AAAABDbBZ6A"
        assert created.at == ts("2026-09-14T08:55:08Z")
        assert created.actor == "xiaowangintel"
        assert created.review_state is None

    def test_draft_transitions_in_order_23_hours_apart(self, normalized):
        _, _, events = normalized
        to_draft = next(e for e in events if e.type == "convert_to_draft")
        to_ready = next(e for e in events if e.type == "ready_for_review")
        created = events[0]

        assert to_draft.at == ts("2026-09-14T08:55:16Z")
        assert to_ready.at == ts("2026-09-15T08:02:46Z")
        # Created ready, drafted 8 seconds later.
        assert (to_draft.at - created.at).total_seconds() == 8
        # ...then ~23.1 hours in draft.
        assert round((to_ready.at - to_draft.at).total_seconds() / 3600, 2) == 23.12
        assert events.index(to_draft) < events.index(to_ready)

    def test_both_approvals(self, normalized):
        _, _, events = normalized
        reviews = [e for e in events if e.type == "review"]
        assert [(e.actor, e.review_state) for e in reviews] == [
            ("liangan1", "APPROVED"),
            ("vkuzo", "APPROVED"),
        ]
        assert [e.at for e in reviews] == [
            ts("2026-09-16T04:42:16Z"),
            ts("2026-09-16T16:43:08Z"),
        ]

    def test_codeowners_tie_broken_by_id(self, normalized):
        _, _, events = normalized
        same_second = [e for e in events if e.at == ts("2026-09-14T08:55:09Z")]
        assert len(same_second) == 3
        assert [e.id for e in same_second] == sorted(e.id for e in same_second)

    def test_merge_and_close_share_a_timestamp(self, normalized):
        _, _, events = normalized
        merged = next(e for e in events if e.type == "merged")
        closed = next(e for e in events if e.type == "closed")
        assert merged.at == closed.at == ts("2026-09-16T16:43:20Z")

    def test_reviewer_class_left_for_derive(self, normalized):
        _, _, events = normalized
        assert all(e.reviewer_class is None for e in events)

    def test_sorted_by_at_then_id(self, normalized):
        _, _, events = normalized
        keys = [(e.at, e.id) for e in events]
        assert keys == sorted(keys)

    def test_event_ids_are_unique(self, normalized):
        _, _, events = normalized
        assert len({e.id for e in events}) == len(events)


class TestFixture4896:
    def test_single_approval_no_draft_events(self):
        meta, snapshot, events = normalize(load_fixture(4896), ref(4896), fetched_at=FETCHED_AT)
        assert meta.author == "akashveramd"
        assert snapshot.state == "MERGED"
        assert len(events) == 7  # 6 timeline + created
        assert not [e for e in events if e.type in {"convert_to_draft", "ready_for_review"}]
        reviews = [e for e in events if e.type == "review"]
        assert [(e.actor, e.review_state) for e in reviews] == [("vkuzo", "APPROVED")]
        assert events[0].type == "created"
        assert events[0].at == ts("2026-09-14T21:12:13Z")


class TestFixture4890:
    def test_open_pr_with_changes_requested(self):
        meta, snapshot, events = normalize(load_fixture(4890), ref(4890), fetched_at=FETCHED_AT)
        assert meta.author == "insoochung"
        assert meta.base_ref == "gh/insoochung/8/base"
        assert snapshot.state == "OPEN"
        assert snapshot.is_draft is False
        assert len(events) == 5  # 4 timeline + created

        review = next(e for e in events if e.type == "review")
        assert review.review_state == "CHANGES_REQUESTED"
        assert review.actor == "andrewor14"
        # submittedAt (…:43Z) wins over createdAt (…:37Z).
        assert review.at == ts("2026-09-16T21:32:43Z")

    def test_no_merged_or_closed_event(self):
        _, _, events = normalize(load_fixture(4890), ref(4890), fetched_at=FETCHED_AT)
        assert not [e for e in events if e.type in {"merged", "closed"}]


class TestFixture4908:
    def test_empty_timeline_yields_only_created(self):
        meta, snapshot, events = normalize(load_fixture(4908), ref(4908), fetched_at=FETCHED_AT)
        assert meta.author == "gss10282025"
        assert snapshot.state == "OPEN"
        assert snapshot.is_draft is True
        assert len(events) == 1
        (created,) = events
        assert created.type == "created"
        assert created.id == "created:PR_kwDOKo_lrs8AAAABD9gzLw"
        assert created.at == ts("2026-09-17T16:35:46Z")
        assert created.actor == "gss10282025"


def synthetic(nodes: list[dict], **overrides) -> dict:
    payload = {
        "id": "PR_test",
        "number": 1,
        "title": "t",
        "state": "OPEN",
        "isDraft": False,
        "createdAt": "2026-01-01T00:00:00Z",
        "author": {"login": "alice"},
        "baseRefName": "main",
        "additions": 1,
        "deletions": 2,
        "changedFiles": 3,
        "timelineItems": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes},
    }
    payload.update(overrides)
    return payload


REF = PRRef(owner="o", repo="r", number=1)


class TestEdgeCases:
    def test_unknown_typename_is_skipped_not_fatal(self):
        _, _, events = normalize(
            synthetic(
                [
                    {
                        "__typename": "AutoMergeEnabledEvent",  # not in TYPENAME_TO_EVENT
                        "id": "AME_1",
                        "createdAt": "2026-01-01T01:00:00Z",
                        "actor": {"login": "bob"},
                    },
                    {
                        "__typename": "MergedEvent",
                        "id": "ME_1",
                        "createdAt": "2026-01-01T02:00:00Z",
                        "actor": {"login": "bob"},
                    },
                ]
            ),
            REF,
            fetched_at=FETCHED_AT,
        )
        assert [e.type for e in events] == ["created", "merged"]

    def test_missing_typename_is_skipped(self):
        _, _, events = normalize(
            synthetic([{"id": "X", "createdAt": "2026-01-01T01:00:00Z"}]),
            REF,
            fetched_at=FETCHED_AT,
        )
        assert [e.type for e in events] == ["created"]

    def test_pending_review_is_dropped(self):
        _, _, events = normalize(
            synthetic(
                [
                    {
                        "__typename": "PullRequestReview",
                        "id": "PRR_pending",
                        "createdAt": "2026-01-01T01:00:00Z",
                        "submittedAt": None,
                        "state": "PENDING",
                        "author": {"login": "carol"},
                    },
                    {
                        "__typename": "PullRequestReview",
                        "id": "PRR_ok",
                        "createdAt": "2026-01-01T02:00:00Z",
                        "submittedAt": "2026-01-01T02:00:05Z",
                        "state": "APPROVED",
                        "author": {"login": "carol"},
                    },
                ]
            ),
            REF,
            fetched_at=FETCHED_AT,
        )
        reviews = [e for e in events if e.type == "review"]
        assert [e.id for e in reviews] == ["PRR_ok"]
        assert reviews[0].at == ts("2026-01-01T02:00:05Z")

    def test_review_falls_back_to_created_at_when_submitted_at_is_null(self):
        _, _, events = normalize(
            synthetic(
                [
                    {
                        "__typename": "PullRequestReview",
                        "id": "PRR_1",
                        "createdAt": "2026-01-01T03:00:00Z",
                        "submittedAt": None,
                        "state": "COMMENTED",
                        "author": {"login": "dave"},
                    }
                ]
            ),
            REF,
            fetched_at=FETCHED_AT,
        )
        review = next(e for e in events if e.type == "review")
        assert review.at == ts("2026-01-01T03:00:00Z")
        assert review.review_state == "COMMENTED"

    def test_dismissed_review_state_is_carried(self):
        _, _, events = normalize(
            synthetic(
                [
                    {
                        "__typename": "PullRequestReview",
                        "id": "PRR_d",
                        "createdAt": "2026-01-01T03:00:00Z",
                        "submittedAt": "2026-01-01T03:00:00Z",
                        "state": "DISMISSED",
                        "author": {"login": "eve"},
                    }
                ]
            ),
            REF,
            fetched_at=FETCHED_AT,
        )
        assert next(e for e in events if e.type == "review").review_state == "DISMISSED"

    @pytest.mark.parametrize("actor", [None, {}, {"login": None}])
    def test_null_actor_tolerated(self, actor):
        _, _, events = normalize(
            synthetic(
                [
                    {
                        "__typename": "ClosedEvent",
                        "id": "CE_1",
                        "createdAt": "2026-01-01T01:00:00Z",
                        "actor": actor,
                    }
                ]
            ),
            REF,
            fetched_at=FETCHED_AT,
        )
        closed = next(e for e in events if e.type == "closed")
        assert closed.actor is None

    def test_null_review_author_tolerated(self):
        _, _, events = normalize(
            synthetic(
                [
                    {
                        "__typename": "PullRequestReview",
                        "id": "PRR_x",
                        "createdAt": "2026-01-01T01:00:00Z",
                        "submittedAt": "2026-01-01T01:00:00Z",
                        "state": "APPROVED",
                        "author": None,
                    }
                ]
            ),
            REF,
            fetched_at=FETCHED_AT,
        )
        assert next(e for e in events if e.type == "review").actor is None

    def test_null_pr_author_gives_none_meta_and_created_actor(self):
        meta, _, events = normalize(synthetic([], author=None), REF, fetched_at=FETCHED_AT)
        assert meta.author is None
        assert events[0].actor is None

    def test_missing_id_falls_back_to_sha1_of_type_at_actor(self):
        import hashlib

        _, _, events = normalize(
            synthetic(
                [
                    {
                        "__typename": "ReopenedEvent",
                        "createdAt": "2026-01-01T01:00:00Z",
                        "actor": {"login": "frank"},
                    }
                ]
            ),
            REF,
            fetched_at=FETCHED_AT,
        )
        reopened = next(e for e in events if e.type == "reopened")
        expected = hashlib.sha1(
            b"reopened|2026-01-01T01:00:00Z|frank", usedforsecurity=False
        ).hexdigest()
        assert reopened.id == expected

    def test_missing_id_and_null_actor_still_deterministic(self):
        node = {"__typename": "ReopenedEvent", "createdAt": "2026-01-01T01:00:00Z", "actor": None}
        first = normalize(synthetic([dict(node)]), REF, fetched_at=FETCHED_AT)[2]
        second = normalize(synthetic([dict(node)]), REF, fetched_at=FETCHED_AT)[2]
        assert first[1].id == second[1].id
        assert len(first[1].id) == 40

    def test_event_without_timestamp_is_skipped(self):
        _, _, events = normalize(
            synthetic([{"__typename": "MergedEvent", "id": "ME_1", "createdAt": None}]),
            REF,
            fetched_at=FETCHED_AT,
        )
        assert [e.type for e in events] == ["created"]

    def test_created_id_falls_back_to_ref_key_without_node_id(self):
        payload = synthetic([])
        del payload["id"]
        _, _, events = normalize(payload, REF, fetched_at=FETCHED_AT)
        assert events[0].id == "created:o/r#1"

    def test_missing_timeline_key_entirely(self):
        payload = synthetic([])
        del payload["timelineItems"]
        _, _, events = normalize(payload, REF, fetched_at=FETCHED_AT)
        assert [e.type for e in events] == ["created"]

    def test_unknown_state_defaults_to_open(self):
        _, snapshot, _ = normalize(synthetic([], state="SOMETHING_NEW"), REF, fetched_at=FETCHED_AT)
        assert snapshot.state == "OPEN"

    def test_missing_counts_default_to_zero(self):
        payload = synthetic([])
        for key in ("additions", "deletions", "changedFiles"):
            del payload[key]
        _, snapshot, _ = normalize(payload, REF, fetched_at=FETCHED_AT)
        assert (snapshot.additions, snapshot.deletions, snapshot.changed_files) == (0, 0, 0)

    def test_is_pure_and_repeatable(self):
        payload = load_fixture(4893)
        first = normalize(payload, ref(4893), fetched_at=FETCHED_AT)
        second = normalize(payload, ref(4893), fetched_at=FETCHED_AT)
        assert first == second

    def test_fetched_at_defaults_to_newest_payload_timestamp(self):
        # Documented fallback: deterministic rather than a clock read.
        _, snapshot, _ = normalize(load_fixture(4893), ref(4893))
        assert snapshot.fetched_at == ts("2026-09-16T16:43:20Z")
