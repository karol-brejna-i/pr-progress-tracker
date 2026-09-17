# AGENTS.md

Working agreements for AI agents in this repository. `CLAUDE.md` points here; this file is
canonical.

## What this project is

A service that polls a watchlist of GitHub PRs, records lifecycle milestones (created,
draft→ready, first review, internal/external approval, changes requested, merged), computes
elapsed-time metrics, commits the data back to this repo, and renders a markdown report as
a scheduled GitHub Action.

**`docs/design.md` is the specification.** Read the section relevant to your task before
writing code. It carries decisions and API traps that are expensive to rediscover — §6.1 in
particular exists because a plausible-looking derivation rule silently reported 0 hours of
draft time for a real pytorch/ao PR that spent 23 hours in draft.

## Toolchain

Runtime is **Python 3.12, standard library only** — the workflow runs `track` and `report`
on bare `python3` with no install step. Do not add a runtime dependency; it breaks that
property. `pytest` and `ruff` are dev-only, managed by `uv` with a committed `uv.lock`.

```
uv run pytest -q                # tests
uv run ruff check .             # lint
uv run ruff format .            # format
python -m pr_tracker verify     # recompute metrics from stored data — offline, safe
```

## Delegate mechanical checks to `runner`

Do not run `pytest` or `ruff` inline. Dispatch `runner` instead. A passing suite is one
token of information (`PASS`) but hundreds of lines of output; a failing one matters only
as the assertion messages. `runner` returns `PASS <cmd>` or `FAIL <cmd>` plus ≤10 lines of
excerpt, which keeps iteration loops from filling the main context with logs you have
already read once.

**When several independent checks apply, dispatch them in a single message** so they run
concurrently — e.g. `runner` for `pytest`, `runner` for `ruff check`, and `data-inspector`
for a data question all go out together rather than in three sequential turns. Only
serialize when one genuinely needs another's result.

## Use a read-only search agent for open-ended exploration

For questions that need sweeping many files or guessing at naming conventions ("where is
draft handling done", "what reads `index.json`"), dispatch `Explore` rather than running a
dozen manual `grep`s. Go direct when you already know the file and symbol — a single
targeted `Read` beats a subagent round trip.

## Never delegate, never run automatically

These have real external side effects. Run them only on explicit user request, in the main
session, never inside a subagent, and never as an unprompted "let me just verify" step:

| Command | Why |
| --- | --- |
| `python -m pr_tracker track` | Hits the live GitHub API with a real token and consumes rate limit. Use captured fixtures for development instead. |
| `git commit` / `git push` of `data/` or `reports/` | The scheduled Action owns these files. A manual commit races it and pollutes the data history. |
| `gh workflow run` | Triggers a real Action that commits to this repo. |
| Any mutating `gh` call (`pr create`, `pr merge`, `pr review`, `api -X POST`…) | Writes to GitHub, possibly to third-party repos we only have read access to. |
| Hand-editing `data/prs/*.json`, `data/index.json`, `data/metrics.csv` | Derived, tracker-owned. Change the derivation and re-run `verify` instead. |
| `uv add`, `uv sync --upgrade` | Would introduce a dependency or move the lock; see the zero-runtime-dependency rule above. |

Reading live GitHub data *is* fine — that is what `gh-probe` is for, scoped to read-only
calls.

## Contracts are the parallelization seam

`src/pr_tracker/contracts.py` (schemas + function signatures) and `cli.py` (wiring) are
owned by the main session. Module agents code *to* the contracts and never edit them; a
mismatch gets reported upward, not worked around locally. This is what lets several
`tracker-dev` agents run at once against disjoint files.

Module ownership: `github.py`+`normalize.py` (fetch/normalize) · `derive.py` (pure
milestones/metrics) · `store.py`+`report.py` (persistence/rendering).

## Agent index

| Agent | Model | Reach for it when |
| --- | --- | --- |
| `runner` | haiku | You need a pass/fail on `pytest`, `ruff`, or `verify`. Not for diagnosing failures. |
| `gh-probe` | haiku | You need live GitHub PR data or a refreshed `tests/fixtures/` file, without tens of KB of JSON in context. Read-only; the only agent that calls `gh`. |
| `data-inspector` | haiku | You have an aggregate question about `data/` or `reports/` spanning many records. Read-only Bash; returns counts, not files. |
| `derive-reviewer` | opus | A change to `derive.py` is about to land, or a metric looks wrong. Reports findings; does not edit. |
| `tracker-dev` | opus | Scoped implementation of one named module against the contracts. |
| `Explore` (built-in) | — | Open-ended, multi-lookup code search where you only need the conclusion. |

None of these run automatically — dispatch them on demand.

### Model tiers: Sonnet is unavailable on this account

The two judgement-tier agents specify `model: opus` rather than the more economical
`sonnet`, because this project's AWS Bedrock credentials carry an **explicit IAM deny** on
`claude-sonnet-4-5` (policy `BedrockMinimalInferenceAccess`). A subagent dispatched with
`model: sonnet` fails immediately with HTTP 403 and produces nothing. Haiku and Opus both
work. If Sonnet access is granted later, `tracker-dev` and `derive-reviewer` are the two to
switch back.
