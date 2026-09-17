# pr-progress-tracker

Tracks lifecycle milestones for a watchlist of GitHub PRs — creation, draft→ready,
first review, internal then external approval, changes requested, merge/close — and the
elapsed-time metrics between them, including the handoff wait between your team's
sign-off and the upstream maintainer's, computed from GitHub's own timeline events rather
than accumulated locally. Runs as a scheduled GitHub Action that commits the results
back into this repo and renders a markdown report.

Full design rationale (why GraphQL timelines, the draft-interval math, the reconstruct-
don't-accumulate model) is in [docs/design.md](docs/design.md). This file is setup and
usage only.

## How it works, in short

Every run re-fetches each watched PR's full GraphQL timeline, merges the events into
the stored record (union by GraphQL node ID, existing events win on conflict), and
recomputes milestones and metrics from scratch. Runs are idempotent: a missed schedule,
a crash mid-run, or a change to the derivation rules all repair themselves on the next
run — nothing is derived by patching previous output. PRs already `MERGED`/`CLOSED` are
skipped on subsequent runs (their data cannot change) unless you pass `--force`.

## Setup

### 1. Requirements

- Python 3.12+ — runtime code (`track`, `report`, `verify`) is **standard library only**,
  no install step. `uv` is only needed for the dev toolchain (tests, lint).
- A GitHub personal access token with read access to every repo you watch.

### 2. Create the token and secret

The built-in Actions `GITHUB_TOKEN` cannot read repos you don't own, so you need your own
token even for public repos.

1. Create a **fine-grained PAT**: <https://github.com/settings/personal-access-tokens/new>
   - Repository access: **Public repositories (read-only)** if everything you watch is
     public. For a private repo in an org you don't own, you additionally need that org
     to allow fine-grained PATs and to approve the grant — if that's not feasible, use a
     GitHub App installation token instead (not implemented here; see
     [docs/design.md §4.2](docs/design.md)).
   - Permissions: none need to be elevated — reading public PR/review/timeline data
     needs no write or admin scope.
   - Fallback if a fine-grained PAT is ever rejected by the GraphQL endpoint: a classic
     PAT with **no scopes at all** also reads public PR data.
2. Add it as a repository secret named **`GH_TRACKER_TOKEN`**: repo → Settings → Secrets
   and variables → Actions → New repository secret.
3. For local runs, export the same token as an environment variable of the same name
   (see [Running locally](#running-locally)).

Cost: the GraphQL query the tracker issues costs 1 point per PR against a 5000/hour
budget, so rate limits are not a practical concern at normal watchlist sizes.

### 3. Populate the config files

All under `config/`, all committed to the repo (this is what the scheduled Action reads —
there's no separate deployment step):

| File | Format | Notes |
| --- | --- | --- |
| `config/prs.txt` | one full PR URL per line, `https://github.com/<owner>/<repo>/pull/<number>` | `#` comments and blank lines ignored. Duplicates collapse, order of first appearance kept. Removing a URL doesn't delete its data — the record is kept and flagged `in_watchlist: false`. |
| `config/internal-reviewers.txt` | one GitHub login per line | **Your own team** — the first review stage. Case-insensitive, leading `@` stripped. An `APPROVED` review by one of these logins satisfies the "internally approved" milestone (first one counts, no N-approval threshold). |
| `config/external-reviewers.txt` | same format | **The upstream repo maintainers** — the second stage, who review after your team has signed off. A login cannot appear in both files — that's a hard config error, not a warning. |
| `config/settings.json` | optional JSON | `stale_review_hours` (default 48), `stale_merge_hours` (default 72), `percentiles` (default `[50, 90]`) — thresholds and percentiles used by the report. Omit the file or any key to take the default. |

The two lists are **the two stages of one review pipeline**, not two independent
audiences: internal reviewers go first, then the PR is handed upstream. Getting them
backwards does not just relabel two columns — it inverts the handoff-latency metric and
the "Review pipeline" section, which count how often that order actually held. The
tracker reports the observed order per PR (`internal_first`, `external_only` for a
bypassed internal review, and so on) rather than assuming it.

Approvals by a login in neither list are classified `other`: recorded and shown as an
informational column, but they satisfy neither approval milestone and never enter the
pipeline accounting.

The repo ships with sample data (`pytorch/ao` PRs) so the pipeline produces a real report
out of the box. **Replace both reviewer files with your actual roster before relying on
the internal/external split for anything.**

A malformed config file (bad URL, JSON syntax error, a login in both reviewer lists)
fails the whole run rather than being silently skipped — a report that looks healthy
while quietly tracking less than it claims is worse than a red build.

### 4. Enable the workflow

[.github/workflows/track.yml](.github/workflows/track.yml) needs:

- `permissions: contents: write` (already set) — it commits `data/` and `reports/` back
  to the branch it ran on.
- Nothing else to configure. It runs every 3 hours by cron and is also
  `workflow_dispatch`-able with a `force` boolean input.

If your default branch is protected against direct pushes, either exempt the workflow's
identity or point it at a branch that isn't (`GITHUB_REF_NAME` is whatever branch
triggered the run).

## Usage

### Running locally

```bash
export GH_TRACKER_TOKEN=ghp_...          # or gh auth token, if you have gh authenticated
PYTHONPATH=src python3 -m pr_tracker track    # fetch + persist; hits the live GitHub API
PYTHONPATH=src python3 -m pr_tracker report   # render data/index.json, data/metrics.csv, reports/pr-progress.md
```

`track` is the only command that touches the network. Run it deliberately, not as a
casual check — it costs real API quota and mutates `data/`.

### Commands

```
python -m pr_tracker track [--force]
python -m pr_tracker report
python -m pr_tracker verify [--write]
```

- **`track`** — fetches every watched PR, merges new events into its stored record, and
  recomputes milestones/metrics. Skips PRs already marked `tracking: final` unless
  `--force`. One PR failing to fetch (bad token scope, repo renamed, deleted PR) doesn't
  sink the run — it's logged, the record's `last_error` is updated, and every other PR
  still gets processed. Exits non-zero only if *every* PR failed.
- **`report`** — offline; rebuilds `data/index.json`, `data/metrics.csv`, and
  `reports/pr-progress.md` from whatever is currently in `data/prs/`. Safe to re-run any
  time, including with no data yet (no-ops cleanly).
- **`verify`** — offline; recomputes every record's milestones/metrics from its stored
  events (using the record's own `snapshot.fetched_at` as the clock, so results are
  reproducible run to run) and reports whether they match what's on disk. Exits non-zero
  on a mismatch. Add `--write` to persist the recomputed values — useful after changing
  the reviewer lists or upgrading `pr_tracker`, since derivation is a pure function of
  stored events and never needs a re-fetch to pick up a rule change.

Exit codes: `0` ok, `1` failure (fetch/data), `2` config error.

### Output

- `data/prs/<owner>__<repo>__<number>.json` — one record per PR: metadata, snapshot,
  full accumulated event history, derived milestones and metrics. This is the durable
  state; everything else is regenerated from it.
- `data/index.json`, `data/metrics.csv` — flat, machine-readable views over all records.
- `reports/pr-progress.md` — the human-facing report: aggregate percentiles, a count of
  how often the internal→external review order actually held, the per-PR milestone table,
  and an attention list that uses `stale_review_hours`/`stale_merge_hours` from
  `settings.json` to flag PRs by *where in the pipeline* they are stuck.

None of these are hand-edited — `track`/`report` own them, and a manual edit will be
overwritten (or, for `data/prs/*.json`, flagged as a mismatch by `verify`).

Durations distinguish two clocks: `hours_draft_total` and the `ready_hours_to_*` metrics
exclude time spent in draft; `wall_hours_to_merge` and `open_hours` are wall-clock. See
[docs/design.md §6](docs/design.md) if a number looks surprising — this split is
deliberate and is where most of the design's subtlety lives.

## Development

```bash
uv sync                       # installs the dev group: pytest, ruff
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

If you're working on this with Claude Code or another AI agent, read
[AGENTS.md](AGENTS.md) first — it documents the module ownership boundaries, the
subagent roster, and commands that must never be run automatically (`track` chief among
them, since it hits the live API with a real token).
