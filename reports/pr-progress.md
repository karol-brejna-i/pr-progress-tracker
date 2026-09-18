# PR progress

Generated 2026-09-18T20:52:46Z · 12 tracked PRs · 10 open (3 draft) · 1 merged · 1 closed

## Aggregates

Medians and percentiles over the PRs whose milestone completed; `n` is how many
of the tracked PRs that is.

| Metric | n / 12 | median | p90 | max |
| --- | --- | --- | --- | --- |
| Draft total | 12 | 5d 18h | 69d 13h | 77d 5h |
| Created → ready | 11 | 0m | 5d 18h | 5d 18h |
| Ready → first review | 8 | 2d 8h | 7d 18h | 12d 21h |
| Ready → internal review | 3 | 12d 21h | 32d 16h | 37d 15h |
| Ready → external review | 3 | 14d 3h | 38d 19h | 45d |
| Ready → internal approval | 3 | 13d 13h | 33d 1h | 37d 22h |
| Ready → external approval | 1 | 11d 23h | 11d 23h | 11d 23h |
| Internal → external approval (handoff) | 1 | 11d 22h | 11d 22h | 11d 22h |
| Created → merge (wall) | 1 | 11d 23h | 11d 23h | 11d 23h |
| Open duration | 12 | 51d 20h | 72d 9h | 98d 14h |

## Review pipeline

Internal reviewers are our own team, who sign off before the PR is handed to the
external repo maintainers. This is how often that order actually held.

| Path | n / 12 |
| --- | --- |
| Internal → external (intended order) | 1 |
| External approved before internal | 0 |
| Internal only — awaiting a maintainer | 3 |
| External only — internal review bypassed | 0 |
| No approval yet | 8 |

## Pull requests

| PR | Author | State | Draft | → Ready | → 1st review | → Internal ✅ | → External ✅ | Handoff | Other ✅ | → Merge | CR | Rounds | Reviewers |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [pytorch/ao#4844](https://github.com/pytorch/ao/pull/4844) | xiaowangintel | MERGED | 0m | 0m | 43m | 43m | 11d 23h | 11d 22h | — | 11d 23h | 0 | 3 | 2 |
| [pytorch/ao#4843](https://github.com/pytorch/ao/pull/4843) | xiaowangintel | OPEN (draft) | ⏳ 21d 10h | 0m | ⏳ 8.0h | ⏳ 8.0h | ⏳ 8.0h | — | — | ⏳ 21d 18h | 0 | 0 | 0 |
| [pytorch/ao#4731](https://github.com/pytorch/ao/pull/4731) | eryk-roch | CLOSED (draft) | ⏳ 36d 11h | 0m | — | — | — | — | — | — | 0 | 0 | 0 |
| [pytorch/ao#4660](https://github.com/pytorch/ao/pull/4660) | karol-brejna-i | OPEN | 17.5h | 17.5h | 3d 21h | ⏳ 49d 10h | ⏳ 49d 10h | — | — | ⏳ 50d 4h | 0 | 0 | 1 |
| [pytorch/ao#4637](https://github.com/pytorch/ao/pull/4637) | karol-brejna-i | OPEN | 11.4h | 0m | 5d 12h | ⏳ 51d 2h | ⏳ 51d 2h | — | — | ⏳ 51d 13h | 0 | 0 | 1 |
| [pytorch/ao#4636](https://github.com/pytorch/ao/pull/4636) | karol-brejna-i | OPEN | 10.8h | 0m | 5d 13h | ⏳ 51d 3h | ⏳ 51d 3h | — | — | ⏳ 51d 13h | 0 | 0 | 1 |
| [pytorch/ao#4630](https://github.com/pytorch/ao/pull/4630) | karol-brejna-i | OPEN | 5d 17h | 5d 17h | 19.0h | ⏳ 46d 9h | ⏳ 46d 9h | — | — | ⏳ 52d 2h | 0 | 0 | 1 |
| [pytorch/ao#4629](https://github.com/pytorch/ao/pull/4629) | karol-brejna-i | OPEN | 5d 18h | 5d 18h | 19.0h | 37d 22h | ⏳ 46d 9h | ⏳ 8d 11h | — | ⏳ 52d 3h | 0 | 2 | 3 |
| [pytorch/ao#4628](https://github.com/pytorch/ao/pull/4628) | karol-brejna-i | OPEN | 5d 18h | 5d 18h | 18.9h | ⏳ 46d 9h | ⏳ 46d 9h | — | — | ⏳ 52d 3h | 0 | 0 | 1 |
| [pytorch/ao#4576](https://github.com/pytorch/ao/pull/4576) | karol-brejna-i | OPEN | 13.0h | 13.0h | ⏳ 64d 1h | ⏳ 64d 1h | ⏳ 64d 1h | — | — | ⏳ 64d 14h | 0 | 0 | 0 |
| [pytorch/ao#4560](https://github.com/pytorch/ao/pull/4560) | draghan | OPEN (draft) | ⏳ 73d 5h | ⏳ 73d 5h | — | — | — | ⏳ 0m | — | ⏳ 73d 5h | 0 | 1 | 2 |
| [pytorch/ao#4477](https://github.com/pytorch/ao/pull/4477) | xiaowangintel | OPEN | 77d 5h | 0m | 12d 21h | 13d 13h | ⏳ 21d 10h | ⏳ 7d 20h | — | ⏳ 98d 14h | 0 | 1 | 2 |

`—` = milestone not reached · `⏳` = still in flight · durations are ready hours (draft time excluded) except `→ Merge`, which is wall clock.

`Handoff` is internal sign-off → maintainer approval. It is blank unless our team approved first: a maintainer who approved without internal review never had a handoff to wait for.

## Attention

### Ready > 48h with no review

- [pytorch/ao#4576](https://github.com/pytorch/ao/pull/4576) — ready 64d 1h, no review yet

### Internally approved > 48h, waiting on a maintainer

- [pytorch/ao#4629](https://github.com/pytorch/ao/pull/4629) — internally approved 8d 11h ago, no maintainer approval
- [pytorch/ao#4477](https://github.com/pytorch/ao/pull/4477) — internally approved 7d 20h ago, no maintainer approval
