"""CLI wiring tests. All offline: `github.fetch_pull_request` is always stubbed.

Each test chdirs into tmp_path, so the relative `config/`, `data/` and `reports/` paths the
CLI uses resolve inside the sandbox and never touch the real repo data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pr_tracker import cli, github, store
from pr_tracker.github import FetchError

FIXTURES = Path(__file__).parent.resolve() / "fixtures"


def payload_for(number: int) -> dict:
    envelope = json.loads((FIXTURES / f"pytorch__ao__{number}.json").read_text())
    return envelope["data"]["repository"]["pullRequest"]


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config"
    config.mkdir()
    (config / "prs.txt").write_text(
        "https://github.com/pytorch/ao/pull/4893\nhttps://github.com/pytorch/ao/pull/4896\n"
    )
    (config / "internal-reviewers.txt").write_text("vkuzo\n")
    (config / "external-reviewers.txt").write_text("liangan1\n")
    monkeypatch.setenv(cli.TOKEN_ENV, "fake-token")
    return tmp_path


def stub_fetch(monkeypatch, handler):
    monkeypatch.setattr(github, "fetch_pull_request", handler)
    monkeypatch.setattr(github, "last_rate_limit", lambda: {"remaining": 4999, "resetAt": "z"})


def fetch_ok(ref, token, **_kwargs):
    return payload_for(ref.number)


class TestTrack:
    def test_writes_a_record_per_pr(self, sandbox, monkeypatch):
        stub_fetch(monkeypatch, fetch_ok)
        assert cli.main(["track"]) == cli.EXIT_OK

        for number in (4893, 4896):
            record = store.load_record(cli.PRRef("pytorch", "ao", number))
            assert record is not None
            assert record.tracking == "final"  # both fixtures are MERGED
            assert record.in_watchlist is True
            assert record.history_runs == 1
            assert record.last_error is None

    def test_missing_token_is_a_config_error(self, sandbox, monkeypatch):
        monkeypatch.delenv(cli.TOKEN_ENV, raising=False)
        assert cli.main(["track"]) == cli.EXIT_CONFIG

    def test_bad_config_is_fatal(self, sandbox, monkeypatch):
        (sandbox / "config" / "prs.txt").write_text("not-a-url\n")
        stub_fetch(monkeypatch, fetch_ok)
        assert cli.main(["track"]) == cli.EXIT_CONFIG

    def test_final_records_are_skipped_on_the_second_run(self, sandbox, monkeypatch):
        calls: list[int] = []

        def counting(ref, token, **kwargs):
            calls.append(ref.number)
            return payload_for(ref.number)

        stub_fetch(monkeypatch, counting)
        cli.main(["track"])
        assert sorted(calls) == [4893, 4896]

        calls.clear()
        cli.main(["track"])
        assert calls == []  # both merged, so nothing re-fetched

        calls.clear()
        cli.main(["track", "--force"])
        assert sorted(calls) == [4893, 4896]

    def test_force_increments_history_runs(self, sandbox, monkeypatch):
        stub_fetch(monkeypatch, fetch_ok)
        cli.main(["track"])
        cli.main(["track", "--force"])
        record = store.load_record(cli.PRRef("pytorch", "ao", 4893))
        assert record.history_runs == 2

    def test_one_failure_does_not_sink_the_run(self, sandbox, monkeypatch):
        def half_broken(ref, token, **kwargs):
            if ref.number == 4893:
                raise FetchError("HTTP 404: not found")
            return payload_for(ref.number)

        stub_fetch(monkeypatch, half_broken)
        assert cli.main(["track"]) == cli.EXIT_OK
        assert store.load_record(cli.PRRef("pytorch", "ao", 4896)) is not None
        assert store.load_record(cli.PRRef("pytorch", "ao", 4893)) is None

    def test_failure_records_last_error_without_losing_data(self, sandbox, monkeypatch):
        stub_fetch(monkeypatch, fetch_ok)
        cli.main(["track"])
        before = store.load_record(cli.PRRef("pytorch", "ao", 4893))

        def always_fails(ref, token, **kwargs):
            raise FetchError("HTTP 403: forbidden")

        stub_fetch(monkeypatch, always_fails)
        cli.main(["track", "--force"])

        after = store.load_record(cli.PRRef("pytorch", "ao", 4893))
        assert after.last_error is not None
        assert "403" in after.last_error
        # Previously collected history survives a failed refresh.
        assert after.events == before.events
        assert after.milestones == before.milestones

    def test_total_failure_returns_nonzero(self, sandbox, monkeypatch):
        def always_fails(ref, token, **kwargs):
            raise FetchError("HTTP 500: boom")

        stub_fetch(monkeypatch, always_fails)
        assert cli.main(["track"]) == cli.EXIT_FAILED

    def test_removal_from_watchlist_flags_but_keeps_data(self, sandbox, monkeypatch):
        stub_fetch(monkeypatch, fetch_ok)
        cli.main(["track"])

        (sandbox / "config" / "prs.txt").write_text("https://github.com/pytorch/ao/pull/4896\n")
        cli.main(["track"])

        dropped = store.load_record(cli.PRRef("pytorch", "ao", 4893))
        assert dropped is not None, "data must be kept"
        assert dropped.in_watchlist is False
        assert store.load_record(cli.PRRef("pytorch", "ao", 4896)).in_watchlist is True

    def test_readding_restores_the_flag(self, sandbox, monkeypatch):
        stub_fetch(monkeypatch, fetch_ok)
        cli.main(["track"])
        (sandbox / "config" / "prs.txt").write_text("https://github.com/pytorch/ao/pull/4896\n")
        cli.main(["track"])
        (sandbox / "config" / "prs.txt").write_text(
            "https://github.com/pytorch/ao/pull/4893\nhttps://github.com/pytorch/ao/pull/4896\n"
        )
        cli.main(["track"])
        assert store.load_record(cli.PRRef("pytorch", "ao", 4893)).in_watchlist is True

    def test_stops_early_below_the_rate_limit_floor(self, sandbox, monkeypatch):
        calls: list[int] = []

        def counting(ref, token, **kwargs):
            calls.append(ref.number)
            return payload_for(ref.number)

        monkeypatch.setattr(github, "fetch_pull_request", counting)
        monkeypatch.setattr(
            github, "last_rate_limit", lambda: {"remaining": 1, "resetAt": "2026-09-17T18:00:00Z"}
        )
        assert cli.main(["track"]) == cli.EXIT_OK
        assert len(calls) == 1, "should break after the first PR"


class TestReportAndVerify:
    def test_report_writes_all_three_outputs(self, sandbox, monkeypatch):
        stub_fetch(monkeypatch, fetch_ok)
        cli.main(["track"])
        assert cli.main(["report"]) == cli.EXIT_OK

        assert cli.REPORT_PATH.exists()
        assert cli.CSV_PATH.exists()
        rows = json.loads(cli.INDEX_PATH.read_text())
        assert len(rows) == 2
        markdown = cli.REPORT_PATH.read_text()
        assert "pytorch/ao#4893" in markdown

    def test_report_with_no_data_is_not_an_error(self, sandbox):
        assert cli.main(["report"]) == cli.EXIT_OK
        assert not cli.REPORT_PATH.exists()

    def test_verify_is_clean_right_after_track(self, sandbox, monkeypatch):
        stub_fetch(monkeypatch, fetch_ok)
        cli.main(["track"])
        assert cli.main(["verify"]) == cli.EXIT_OK

    def test_verify_detects_tampered_metrics(self, sandbox, monkeypatch):
        stub_fetch(monkeypatch, fetch_ok)
        cli.main(["track"])

        ref = cli.PRRef("pytorch", "ao", 4893)
        record = store.load_record(ref)
        record.metrics.ready_hours_to_internal_approval = 999.0
        store.save_record(record)

        assert cli.main(["verify"]) == cli.EXIT_FAILED
        # Read-only by default: the bad value is reported, not silently corrected.
        assert store.load_record(ref).metrics.ready_hours_to_internal_approval == 999.0

        assert cli.main(["verify", "--write"]) == cli.EXIT_OK
        assert store.load_record(ref).metrics.ready_hours_to_internal_approval == 32.67

    def test_verify_uses_fetched_at_so_it_is_reproducible(self, sandbox, monkeypatch):
        """Recomputing with the record's own fetched_at means a later clock cannot make a
        clean dataset look drifted."""
        stub_fetch(monkeypatch, fetch_ok)
        cli.main(["track"])
        assert cli.main(["verify"]) == cli.EXIT_OK
        assert cli.main(["verify"]) == cli.EXIT_OK
