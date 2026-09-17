"""Command line entry points: `track`, `report`, `verify`. Owned by the main session.

    python -m pr_tracker track [--force]
    python -m pr_tracker report
    python -m pr_tracker verify [--write]

`track` is the only mode that touches the network.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from pr_tracker import github, report, store
from pr_tracker.contracts import PRRecord, PRRef
from pr_tracker.derive import derive
from pr_tracker.github import FetchError
from pr_tracker.normalize import normalize
from pr_tracker.watchlist import ConfigError, load_config, load_watchlist

TOKEN_ENV = "GH_TRACKER_TOKEN"
INDEX_PATH = Path("data/index.json")
CSV_PATH = Path("data/metrics.csv")
REPORT_PATH = Path("reports/pr-progress.md")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2


def _log(message: str) -> None:
    print(message, flush=True)


def cmd_track(args: argparse.Namespace) -> int:
    cfg = load_config()
    refs = load_watchlist()

    token = os.environ.get(TOKEN_ENV)
    if not token:
        _log(f"error: {TOKEN_ENV} is not set (needs read access to the watched repos)")
        return EXIT_CONFIG

    now = datetime.now(UTC)
    watched: set[str] = {ref.key for ref in refs}
    fetched = skipped = 0
    failures: list[tuple[PRRef, str]] = []

    for ref in refs:
        existing = store.load_record(ref)

        if existing is not None and existing.tracking == "final" and not args.force:
            # Merged/closed PRs cannot change. Skipping them keeps run time flat as the
            # watchlist grows; --force re-fetches everything.
            if not existing.in_watchlist:
                existing.in_watchlist = True
                store.save_record(existing)
            skipped += 1
            continue

        try:
            payload = github.fetch_pull_request(ref, token)
        except FetchError as exc:
            # One unreadable repo must not hide the report for every other PR.
            failures.append((ref, str(exc)))
            _log(f"  FAIL {ref.key}: {exc}")
            if existing is not None:
                existing.last_error = f"{now.isoformat()}: {exc}"
                store.save_record(existing)
            continue

        meta, snapshot, events = normalize(payload, ref, fetched_at=now)
        merged = store.merge_events(existing.events if existing else [], events)
        milestones, metrics = derive(merged, snapshot, cfg, now)

        store.save_record(
            PRRecord(
                ref=ref,
                meta=meta,
                snapshot=snapshot,
                events=merged,
                milestones=milestones,
                metrics=metrics,
                in_watchlist=True,
                tracking="final" if snapshot.state in ("MERGED", "CLOSED") else "active",
                last_error=None,
                history_runs=(existing.history_runs + 1) if existing else 1,
            )
        )
        fetched += 1
        _log(f"  ok   {ref.key} ({snapshot.state}, {len(merged)} events)")

        rate = github.last_rate_limit()
        if rate is not None and rate.get("remaining", 1 << 30) < github.RATE_LIMIT_FLOOR:
            _log(
                f"stopping early: rate limit remaining={rate.get('remaining')} "
                f"below floor {github.RATE_LIMIT_FLOOR} (resets {rate.get('resetAt')})"
            )
            break

    # A URL removed from the watchlist keeps its data; it is only flagged. History is cheap.
    for record in store.load_all_records():
        if record.ref.key not in watched and record.in_watchlist:
            record.in_watchlist = False
            store.save_record(record)
            _log(f"  note {record.ref.key} no longer in watchlist (data kept)")

    _log(f"track: {fetched} fetched, {skipped} skipped, {len(failures)} failed")

    # Fail the run only if nothing at all succeeded — a partial result is still useful.
    if failures and fetched == 0 and skipped == 0:
        return EXIT_FAILED
    return EXIT_OK


def cmd_report(_args: argparse.Namespace) -> int:
    cfg = load_config()
    records = store.load_all_records()
    if not records:
        _log("report: no records in data/prs — run `track` first")
        return EXIT_OK

    rows = report.build_index(records)
    store.write_json_atomic(INDEX_PATH, rows)
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    report.write_csv(rows, CSV_PATH)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        report.render_markdown(records, cfg, datetime.now(UTC)), encoding="utf-8"
    )

    _log(f"report: {len(records)} records -> {REPORT_PATH}, {INDEX_PATH}, {CSV_PATH}")
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    """Recompute milestones and metrics from stored events. No network.

    Each record is recomputed with its own `snapshot.fetched_at` as `now`, so the result is
    reproducible and any difference is a genuine derivation change rather than the clock
    having moved on.
    """
    cfg = load_config()
    records = store.load_all_records()
    mismatched = 0

    for record in records:
        as_of = record.snapshot.fetched_at
        milestones, metrics = derive(record.events, record.snapshot, cfg, as_of)
        if milestones == record.milestones and metrics == record.metrics:
            continue

        mismatched += 1
        _log(f"  differs {record.ref.key}")
        if milestones != record.milestones:
            _log(f"    milestones stored={record.milestones}")
            _log(f"    milestones recomputed={milestones}")
        if metrics != record.metrics:
            _log(f"    metrics stored={record.metrics}")
            _log(f"    metrics recomputed={metrics}")

        if args.write:
            record.milestones = milestones
            record.metrics = metrics
            store.save_record(record)

    if args.write:
        _log(f"verify: {len(records)} checked, {mismatched} rewritten")
        return EXIT_OK

    _log(f"verify: {len(records)} checked, {mismatched} differ")
    return EXIT_FAILED if mismatched else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pr_tracker", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    track = sub.add_parser("track", help="fetch watched PRs and persist their records")
    track.add_argument(
        "--force",
        action="store_true",
        help="re-fetch PRs already marked final (merged/closed)",
    )
    track.set_defaults(func=cmd_track)

    rep = sub.add_parser("report", help="render index.json, metrics.csv and the markdown report")
    rep.set_defaults(func=cmd_report)

    ver = sub.add_parser("verify", help="recompute derived fields from stored data (offline)")
    ver.add_argument("--write", action="store_true", help="persist recomputed values")
    ver.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        # Configuration problems are always fatal: a silently dropped PR still renders a
        # healthy-looking report while tracking less than it claims.
        _log(f"config error: {exc}")
        return EXIT_CONFIG
    except store.StoreError as exc:
        _log(f"data error: {exc}")
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
