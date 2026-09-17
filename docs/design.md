# PR Progress Tracker — Design Proposal

*Created: 2026-09-17 18:41 CEST · Updated: 2026-09-17 20:52 CEST*

## 1. Purpose and scope

A service that periodically polls a watchlist of GitHub pull requests, records their
lifecycle milestones, computes elapsed-time metrics, persists everything in the hosting
repository, and renders a markdown report.

**Phase 1 (this design's focus)**

- Watchlist of PR URLs, possibly across many repos and orgs.
- Scheduled GitHub Actions run: fetch → derive milestones → persist → render → commit.
- Per-PR JSON records with append-only event history, plus a flat rollup for reporting.
- One generated markdown report with per-PR rows and aggregate statistics.

**Phase 2 (designed for, not built now)**

- GitHub Pages dashboard reading the JSON/CSV rollup, with client-side filtering.
- Watchlist editing via issue/PR forms or a small API.

**Non-goals**

- Real-time/webhook ingestion. Polling is sufficient; all needed history is
  reconstructible from the GitHub timeline at any time.
- A database server, an external service, or per-review-comment analytics.
- Business-hours/SLA-aware durations (noted as an extension in §9).

## 2. Key design decision: reconstruct, don't accumulate

Every run re-fetches each PR's **full** timeline and recomputes milestones from scratch,
then merges events into the stored record by stable event ID.

This matters because it makes the whole system idempotent and self-healing: a missed run,
a crashed run, a bug in the derivation rules, or a new milestone added later all repair
themselves on the next execution — no backfill scripts, no incremental cursors to corrupt.
PR timelines are small and bounded (tens of events), so the cost is negligible.

The stored event history is therefore not the source of truth for *milestones* — it is an
audit trail plus a hedge against GitHub eventually trimming timeline data.

Consequence: **derivation must be a pure function** `(events, reviewer_lists) → milestones`,
with no I/O and no dependence on the previous record. This is the part to unit-test.

## 3. Inputs (committed configuration)

```
config/prs.txt                  # one PR URL per line; # comments and blank lines allowed
config/internal-reviewers.txt   # one GitHub login per line — our own team
config/external-reviewers.txt   # one GitHub login per line — the upstream maintainers
config/settings.json            # optional: report window, percentiles, stale thresholds
```

- PR URL format: `https://github.com/{owner}/{repo}/pull/{number}` → parsed into a
  `(owner, repo, number)` triple. Reject anything else loudly (fail the run) — a typo'd
  URL silently dropped is worse than a red build.
- The two lists are the two **stages of one review pipeline**, not two independent audiences:
  internal reviewers are our own teammates, who review first, and external reviewers are the
  repo maintainers the PR is handed to afterwards. The order is what §6.2 measures.
- Logins are compared **case-insensitively**; a leading `@` is stripped.
- A login in both lists is a config error → fail the run.
- Approvals by logins in neither list are classified `other` and satisfy **neither** the
  internal nor the external approval milestone. They are still recorded, so a report can
  show "approved by someone, but not by a tracked reviewer".
- Removing a URL from `config/prs.txt` does **not** delete its data file; the record is
  marked `"in_watchlist": false` and excluded from the default report. History is cheap;
  losing it is not.

## 4. Data collection

### 4.1 GraphQL over REST

One GraphQL query per PR replaces 3–4 REST calls (PR, reviews, timeline pages) and, more
importantly, is the only place where the **draft→ready transition** is exposed as a
timestamped event (`ReadyForReviewEvent`). REST's `GET /pulls/{n}` only reports the current
`draft` boolean; the issue timeline endpoint has the event but needs a separate preview-ish
call anyway.

Query (parameterized; `timelineItems` page size max is 100, so paginate on `pageInfo`):

```graphql
query($owner:String!, $repo:String!, $number:Int!, $cursor:String) {
  rateLimit { cost remaining resetAt }
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$number) {
      id number title url state isDraft
      createdAt closedAt mergedAt
      author { login }
      baseRefName additions deletions changedFiles
      timelineItems(first:100, after:$cursor, itemTypes:[
        READY_FOR_REVIEW_EVENT, CONVERT_TO_DRAFT_EVENT,
        PULL_REQUEST_REVIEW, REVIEW_REQUESTED_EVENT,
        REVIEW_REQUEST_REMOVED_EVENT, REVIEW_DISMISSED_EVENT,
        MERGED_EVENT, CLOSED_EVENT, REOPENED_EVENT
      ]) {
        pageInfo { hasNextPage endCursor }
        nodes {
          __typename
          ... on ReadyForReviewEvent    { id createdAt actor { login } }
          ... on ConvertToDraftEvent    { id createdAt actor { login } }
          ... on PullRequestReview      { id createdAt submittedAt state author { login } }
          ... on ReviewRequestedEvent   { id createdAt actor { login }
                                          requestedReviewer { __typename
                                            ... on User { login } ... on Team { name } } }
          ... on ReviewRequestRemovedEvent { id createdAt actor { login } }
          ... on ReviewDismissedEvent   { id createdAt actor { login } }
          ... on MergedEvent            { id createdAt actor { login } }
          ... on ClosedEvent            { id createdAt actor { login } }
          ... on ReopenedEvent          { id createdAt actor { login } }
        }
      }
    }
  }
}
```

Notes that will bite whoever implements this:

- `PullRequestReview.state` is one of `PENDING | COMMENTED | APPROVED | CHANGES_REQUESTED |
  DISMISSED`. Use `submittedAt` as the event time and fall back to `createdAt`; skip
  `PENDING` reviews entirely (they are unsubmitted drafts visible only to their author).
- A dismissed approval appears as `state: DISMISSED` on re-fetch, and the original approval
  timestamp is lost from the API. This is a real reason to keep the append-only event log:
  the earlier `APPROVED` snapshot survives locally. Derivation prefers the stored history.
- `pullRequest.publishedAt` is **not** the ready-for-review time. Do not use it.
- Do not also request the `reviews` connection — it duplicates the timeline reviews and
  invites two sources of truth.

### 4.2 Transport and auth

- **Transport:** Python 3.12 standard library (`urllib.request`) posting to
  `https://api.github.com/graphql`. Zero dependency install on the runner, full control
  over retries/pagination, and the fetch layer stays mockable in tests. `gh api graphql`
  is fine for ad-hoc debugging but adds subprocess/JSON-plumbing for no gain in CI.
- **Auth:** the workflow's built-in `GITHUB_TOKEN` cannot read other repositories, so a
  cross-repo credential is required. Two options:
  - *Fine-grained PAT* (`secrets.PR_TRACKER_TOKEN`) with read-only **Pull requests** +
    **Contents** on the watched repos. Simplest; needs org approval for org-owned repos and
    a human owns the expiry.
  - *GitHub App* installation token minted per run via `actions/create-github-app-token`.
    More setup, but no expiring PAT and per-repo installation control. **Recommended once
    the watchlist spans more than one org.**
- **Retries:** exponential backoff on 5xx, `403`/`429` with `Retry-After`, and secondary
  rate-limit messages. Abort the run early if `rateLimit.remaining` drops below a floor
  (e.g. 100) and report partial success rather than hammering the API.
- **Cost:** ~1–2 GraphQL points per PR against a 5000 points/hour budget — hundreds of PRs
  per run are fine.

### 4.3 Skipping finished PRs

Once a PR is `MERGED` or `CLOSED` **and** has been fetched at least once after reaching
that state, set `"tracking": "final"` and skip it on subsequent runs (a `--force` /
`workflow_dispatch` input re-fetches everything). This keeps run time flat as the watchlist
grows and makes recomputation of *derived* fields still possible offline from stored events.

## 5. Data model and storage

### 5.1 Layout

```
data/prs/{owner}__{repo}__{number}.json   # one record per PR, source of truth
data/index.json                            # rollup: array of flat summaries (Pages-ready)
data/metrics.csv                           # same rollup, spreadsheet-friendly
reports/pr-progress.md                     # generated report
```

One file per PR is deliberate: concurrent-ish runs and human edits produce clean, tiny git
diffs, and a corrupt write damages one PR instead of the whole dataset. `index.json` and
`metrics.csv` are fully derived — they can be regenerated from `data/prs/` at any time and
are never edited by hand.

### 5.2 Per-PR record

```json
{
  "schema_version": 1,
  "key": "octo/api#123",
  "owner": "octo", "repo": "api", "number": 123,
  "url": "https://github.com/octo/api/pull/123",
  "title": "Add retry to publisher",
  "author": "alice",
  "base_ref": "main",
  "in_watchlist": true,
  "tracking": "active",
  "snapshot": {
    "state": "OPEN", "is_draft": false,
    "additions": 210, "deletions": 34, "changed_files": 7,
    "fetched_at": "2026-09-17T16:05:11Z"
  },
  "events": [
    { "id": "PR_kwDO...", "type": "created",            "at": "2026-09-10T08:00:00Z", "actor": "alice" },
    { "id": "RFR_lADO...", "type": "ready_for_review",  "at": "2026-09-11T09:15:00Z", "actor": "alice" },
    { "id": "PRR_kwDO...", "type": "review",            "at": "2026-09-11T14:02:00Z", "actor": "bob",
      "review_state": "CHANGES_REQUESTED", "reviewer_class": "internal" },
    { "id": "PRR_kwDO...", "type": "review",            "at": "2026-09-12T10:30:00Z", "actor": "bob",
      "review_state": "APPROVED", "reviewer_class": "internal" }
  ],
  "milestones": {
    "created_at":            "2026-09-10T08:00:00Z",
    "ready_at":              "2026-09-11T09:15:00Z",
    "draft_at_creation":     true,
    "draft_intervals":       [["2026-09-10T08:00:00Z", "2026-09-11T09:15:00Z"]],
    "first_review_at":       "2026-09-11T14:02:00Z",
    "first_internal_review_at":   "2026-09-11T14:02:00Z",
    "first_external_review_at":   null,
    "first_changes_requested_at": "2026-09-11T14:02:00Z",
    "internal_approved_at":  "2026-09-12T10:30:00Z",
    "external_approved_at":  null,
    "other_approved_at":     null,
    "approval_path":         "internal_only",
    "merged_at":             null,
    "closed_at":             null
  },
  "metrics": {
    "hours_draft_total":           25.25,
    "hours_created_to_ready":      25.25,
    "ready_hours_to_first_review": 4.78,
    "ready_hours_to_internal_review":  4.78,
    "ready_hours_to_external_review":  null,
    "ready_hours_to_internal_approval": 24.0,
    "ready_hours_to_external_approval": null,
    "ready_hours_internal_to_external_approval": null,
    "wall_hours_to_merge":         null,
    "changes_requested_count":     1,
    "review_rounds":               2,
    "distinct_reviewers":          1,
    "open_hours":                  200.1
  },
  "last_error": null,
  "history_runs": 14
}
```

- All timestamps are UTC ISO-8601 with `Z`. No local time anywhere in storage; formatting
  is the report's job.
- `events` is sorted by `at`, then `id`, and **merged by `id`** on each run — a normalized,
  stable projection of the GraphQL timeline, not the raw payload. `created` is synthesized
  from `pullRequest.createdAt` (GitHub emits no event for PR creation).
- Events lacking an `id` (should not happen with the item types above) fall back to a
  synthetic `sha1(type|at|actor)`.
- `reviewer_class` ∈ `internal | external | other`, resolved at write time **and**
  recomputed on read, so a reviewer-list change takes effect immediately.
- `metrics` values are hours as floats, `null` when the milestone hasn't happened.

### 5.3 Committing back

The workflow commits `data/` and `reports/` with `[skip ci]` in the message and a
`concurrency` group so runs can't race. Before pushing: `git pull --rebase --autostash`,
retry the push once. If there is no diff, skip the commit entirely — otherwise the repo
fills with empty churn.

## 6. Derivation rules

`derive(events, config) → (milestones, metrics)`, pure, applied to the merged event list.

| Milestone | Rule |
| --- | --- |
| `created_at` | `pullRequest.createdAt` |
| `draft_at_creation` | `snapshot.is_draft XOR (count of draft/ready transition events is odd)` — see §6.1 |
| `draft_intervals` | forward replay of the draft state machine from `draft_at_creation` |
| `ready_at` | first moment the PR was in the ready state: `created_at` if not `draft_at_creation`, else the first `ready_for_review` event |
| `first_review_at` | earliest non-`PENDING` review of any state, from any reviewer class |
| `first_internal_review_at` / `first_external_review_at` | the same rule restricted to one class — see §6.2 |
| `first_changes_requested_at` | earliest review with `CHANGES_REQUESTED` |
| `internal_approved_at` | earliest `APPROVED` review whose author is in the internal list |
| `external_approved_at` | earliest `APPROVED` review whose author is in the external list |
| `approval_path` | the observed order of those two approvals — see §6.2 |
| `merged_at` / `closed_at` | from the `merged`/`closed` timeline events, with `snapshot.state` deciding which applies (`MERGED` is checked first). The PR object's own `mergedAt`/`closedAt` are not carried on `Snapshot`; the timeline is fully paginated, so the events are equally authoritative. |

### 6.1 Draft time is an interval problem, not a milestone

GitHub does not expose whether a PR was *created* as a draft — only its current `isDraft`
and the transition events. Recover the initial state by parity: every
`ReadyForReviewEvent`/`ConvertToDraftEvent` flips draft state, so

```
draft_at_creation = snapshot.is_draft XOR (len(transition_events) % 2 == 1)
```

Then replay forward from that initial state to build `draft_intervals`.

This matters because a PR can re-enter draft after being ready, and **it happens in
practice**: `pytorch/ao#4893` was created ready, converted to draft 8 seconds later, and
readied ~23 hours after that. A first-transition-only rule reports zero draft time for it
and charges those 23 hours to review latency.

So review-side durations are measured in **ready hours** — wall-clock elapsed minus any
overlap with `draft_intervals` — via a small interval-subtraction helper. `wall_hours_to_merge`
stays wall-clock, because "lead time" means calendar time to whoever asks for it. Reporting
both, plus `hours_draft_total`, keeps the two readings distinguishable instead of blended.

Remaining edge cases, decided explicitly:

- **Never a draft** → `draft_intervals = []`, ready hours equal wall hours.
- **Still a draft** → the final draft interval is left open-ended (`end: null`) and closed at
  `merged_at or closed_at or now` — the same clock `open_hours` uses. A PR closed or merged
  while still a draft stops accruing draft hours at that moment, not at whatever `now`
  happens to be on a later read; `now` only applies while the PR is genuinely still open.
  `open_hours` measures from creation.
- **Never ready** (created as a draft and still one) → `ready_at` is `null` and every
  review-side metric is `null`. This is the only case that nulls them.
- **Ready, reviewed, then pushed back to draft** → `ready_at` and the approval metrics are
  *kept*. The approval genuinely happened and its latency is a measured fact; discarding it
  because the PR later re-entered draft would destroy real data. Being a draft *now* says
  nothing about what already occurred.
- **`closed_at` is `null` for a merged PR.** A merged PR is also closed, and GitHub sets
  `closedAt` on it, but recording that here would reintroduce the merged-also-emits-
  `ClosedEvent` trap at every read site. `closed_at` therefore means "closed without
  merging"; check `merged_at` for the merge. `open_hours` uses `merged_at or closed_at or now`.
- **Approval dismissed, then re-approved** → keep the *earliest* approval from the stored
  history (approval was reached once), and expose `changes_requested_count`/`review_rounds`
  so a dismissed approval is still visible in the report.
- **Merged without approval** (admin merge, no required review) → approval milestones stay
  `null`; the report shows this as a distinct case rather than a zero.
- **Reopened PR** → `closed_at` reflects the current state only; the reopen appears in
  `events`.
- **A merged PR also emits `ClosedEvent`**, ~1 second after `MergedEvent` (confirmed on
  pytorch/ao). Never infer "closed without merging" from the presence of a close event —
  branch on `state == MERGED` first.
- **CODEOWNERS auto-requests reviewers at creation** — pytorch/ao PRs carry three
  `ReviewRequestedEvent`s one second after `createdAt`. A future
  `time_to_first_review_request` metric would be ~0 and meaningless there; prefer
  time-to-first-*review*.
- **Durations measured from `ready_at`, not `created_at`**, for review-side metrics — a PR
  nobody could review yet shouldn't be penalized for sitting in draft. `hours_to_merge` is
  measured from `created_at` because that is the number people mean by "lead time".
- Negative durations (clock skew, out-of-order data) are clamped to 0 and logged. *Both* halves
  matter: an unlogged clamp is indistinguishable from a genuine zero, so no duration helper may
  short-circuit a reversed span before it reaches the clamp — `timeutil.hours` is the one place
  that decision is made.

### 6.2 Review is a two-stage pipeline, not two parallel audiences

The two reviewer lists are sequential stages: our own team signs off, then the PR is handed
to the repo maintainers. Measuring both approvals independently from `ready_at` — the
obvious reading of §6's table — makes three real things unexpressible:

- **The handoff wait.** Most of `ready_hours_to_external_approval` on a PR that followed the
  process is just our own review time. `ready_hours_internal_to_external_approval` isolates
  the stretch we wait through but do not control.
- **A bypassed pipeline.** A maintainer approving with no internal sign-off renders
  identically to "internal approval not reached yet": an em dash in both cases.
- **Which side is slow.** `first_review_at` blends the classes (and includes `other`), so
  "how fast do we pick things up" and "how fast do the maintainers respond" are one number.

`approval_path` records the sequence as a fact, not a judgement — `external_only` and
`external_first` are normal on repos we do not control, and are worth *seeing*:

| Value | Meaning |
| --- | --- |
| `none` | neither class has approved yet |
| `internal_only` | our team approved; still waiting on a maintainer |
| `external_only` | a maintainer approved with no internal sign-off — pipeline bypassed |
| `internal_first` | both approved, in the intended order (including simultaneously) |
| `external_first` | both approved, but the maintainer got there first |

The boundary is `internal_at <= external_at`: two approvals bearing the same timestamp — GitHub
records seconds, so bulk or scripted approvals can genuinely tie — count as the intended order,
with a handoff of `0.0`. That zero is literally true, unlike the `external_first` zero below.

`ready_hours_internal_to_external_approval` is `null` unless `approval_path ==
"internal_first"`. For `external_first` the span is negative and would clamp to `0.0`, which
reads as "handed off instantly" when what actually happened is the opposite; `approval_path`
already carries that fact, and a null is honest where a zero is not. Like the other
review-side metrics it is measured in ready hours, so a draft dip between the two approvals
is not charged to maintainer latency.

An `other` approval (a login in neither list) never enters the pipeline: it fills
`other_approved_at` and leaves `approval_path` untouched.

Consumers recompute the path from the two timestamps rather than trusting the stored field.
A record written before `approval_path` existed loads with the `"none"` default until
`verify --write` backfills it, and a report that silently dropped such a PR from its counts
and its attention list would be exactly the quiet failure this design refuses elsewhere.

## 7. Pipeline and repo layout

```
.github/workflows/track.yml
config/…                    # §3
src/pr_tracker/
  __init__.py
  cli.py                    # `python -m pr_tracker track|report|verify`
  watchlist.py              # parse config/prs.txt + reviewer lists
  github.py                 # GraphQL client: auth, pagination, retries, rate limit
  normalize.py              # GraphQL payload → normalized events
  derive.py                 # PURE: events → milestones + metrics
  store.py                  # read/merge/write data/prs/*.json, rollups
  report.py                 # index.json + metrics.csv + reports/pr-progress.md
tests/
  fixtures/*.json           # captured GraphQL payloads
  test_derive.py            # golden tests: fixture → expected milestones
data/  reports/  docs/
```

Run stages, per PR, errors isolated:

1. **Load** watchlist + reviewer lists (fail fast on malformed config).
2. **Fetch** PR via GraphQL (skip if `tracking == "final"` and not forced).
3. **Normalize** payload to events.
4. **Merge** into the existing record by event ID.
5. **Derive** milestones and metrics.
6. **Write** the per-PR JSON.
7. After all PRs: **render** `index.json`, `metrics.csv`, `reports/pr-progress.md`.
8. **Commit and push**.

A PR that fails (404, no permission, rate limit) records `last_error` with a timestamp,
keeps its previous data, and does not abort the run. The workflow exits non-zero only if
*every* PR failed or config was invalid — a single unreadable repo shouldn't hide the
report for the other 40 PRs. The report itself carries a "problems" section, so failures
stay visible rather than being swallowed by a green build.

`verify` is a data-only mode: recompute milestones/metrics from stored events without any
network access. Useful for CI on rule changes and for validating a derivation edit against
the whole dataset locally.

## 8. Workflow sketch

```yaml
name: Track PR progress
on:
  schedule: [{ cron: "17 */3 * * *" }]     # every 3h, off the hour
  workflow_dispatch:
    inputs:
      force: { description: "Re-fetch finalized PRs", type: boolean, default: false }
permissions:
  contents: write
concurrency:
  group: pr-tracker
  cancel-in-progress: false
jobs:
  track:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: python -m pr_tracker track ${{ inputs.force && '--force' || '' }}
        env: { GH_TRACKER_TOKEN: ${{ secrets.PR_TRACKER_TOKEN }} }
      - run: python -m pr_tracker report
      - name: Commit
        run: |
          git config user.name  "pr-progress-tracker[bot]"
          git config user.email "pr-progress-tracker@users.noreply.github.com"
          git add data reports
          git diff --cached --quiet && exit 0
          git commit -m "chore(data): update PR progress [skip ci]"
          git pull --rebase --autostash
          git push
```

Cadence: every 3 hours is a reasonable default — hour-level resolution is well below the
granularity of any question being asked here, and scheduled workflows on a busy runner
queue can be delayed anyway. Timestamps come from GitHub's own event data, so a late run
does not distort the metrics; it only delays the report.

## 9. Presentation (phase 1)

`reports/pr-progress.md`, regenerated in full every run:

1. **Header** — generation timestamp, PR counts by state, run problems (if any).
2. **Aggregates** — for each metric: n, median, p90, max. Median and p90 rather than mean,
   because a single stalled PR wrecks a mean. Reported over the whole watchlist and over
   PRs whose relevant milestone completed.
3. **Review pipeline** — a count per `approval_path` value (§6.2). A process metric, not an
   alert: it is the only place a bypassed internal review is distinguishable from one that
   simply has not happened yet, since both are an em dash in the table below.
4. **Per-PR table** — PR link, author, state, and each metric formatted as `2d 4h`, with
   `—` for not-yet-reached and `⏳ 3d` for in-flight. The `Handoff` column is blank unless
   our team approved first; its in-flight clock runs only while `approval_path ==
   "internal_only"`, because with no internal sign-off there is nothing to measure from.
5. **Attention list** — four buckets, keyed to *where in the pipeline* the PR is stuck:
   ready >N hours with no review; internally approved >N hours with no maintainer approval
   (nudge upstream); **fully** approved but unmerged >N hours (a merge-step problem, so both
   classes must have signed off — a half-approved PR is mid-pipeline, not stuck at the
   merge); changes requested with no follow-up review. Thresholds from
   `config/settings.json`.

Phase 2 hook: the dashboard consumes `data/index.json` directly, so the markdown renderer
and the future web UI share the same derived rollup and can't drift.

## 10. Extensions, deliberately deferred

- Business-hours / working-day durations, and holiday calendars.
- `time_to_first_review_request` (from `ReviewRequestedEvent`) — data is already collected.
- Per-reviewer statistics and CODEOWNERS-derived reviewer lists instead of static text files.
- Team-level approvals (`requestedReviewer` on `Team`) as an approval milestone.
- Trend series over time (the git history of `data/` already makes this reconstructible).
- Required-check / CI-duration milestones.

## 11. Decisions (previously open questions)

Settled by the owner on 2026-09-17:

1. **Auth — fine-grained PAT.** The first watched repo, `pytorch/ao`, is **public**, so a
   fine-grained PAT owned by the operator's own account with repository access set to
   *Public repositories (read-only)* is sufficient; no permission grant from the `pytorch`
   org is needed, because reading public PR/review/timeline data requires no privileged
   scope. Verified against the live API: the §4.1 query costs **1 GraphQL point per PR**
   against a 5000/hour budget.
   - Fallback if a fine-grained PAT is ever rejected by the GraphQL endpoint: a **classic
     PAT with no scopes at all** also reads public PR data.
   - Revisit only when a **private** repo in an org the operator doesn't own joins the
     watchlist — fine-grained PATs then need that org to allow them (and usually an admin
     approval), which is the point where a GitHub App becomes worth the setup.
2. **Approval semantics — the first approval counts.** `internal_approved_at` is the
   earliest `APPROVED` review by an internal-list login; no N-approvals threshold.
3. **`other` approvals — informational column.** Recorded as `other_approved_at` and shown
   in the report as an informational column; they do not satisfy the internal or external
   approval milestone.
