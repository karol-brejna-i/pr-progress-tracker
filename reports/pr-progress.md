# PR progress

Generated 2026-09-17T18:58:18Z · 7 tracked PRs · 7 open (0 draft) · 0 merged · 0 closed

## Aggregates

Medians and percentiles over the PRs whose milestone completed; `n` is how many
of the tracked PRs that is.

| Metric | n / 7 | median | p90 | max |
| --- | --- | --- | --- | --- |
| Draft total | 7 | 17.5h | 5d 18h | 5d 18h |
| Created → ready | 7 | 17.5h | 5d 18h | 5d 18h |
| Ready → first review | 6 | 2d 8h | 5d 12h | 5d 13h |
| Ready → internal review | 1 | 37d 15h | 37d 15h | 37d 15h |
| Ready → external review | 1 | 45d | 45d | 45d |
| Ready → internal approval | 1 | 37d 22h | 37d 22h | 37d 22h |
| Ready → external approval | 0 | — | — | — |
| Internal → external approval (handoff) | 0 | — | — | — |
| Created → merge (wall) | 0 | — | — | — |
| Open duration | 7 | 51d 1h | 56d 1h | 63d 12h |

## Review pipeline

Internal reviewers are our own team, who sign off before the PR is handed to the
external repo maintainers. This is how often that order actually held.

| Path | n / 7 |
| --- | --- |
| Internal → external (intended order) | 0 |
| External approved before internal | 0 |
| Internal only — awaiting a maintainer | 1 |
| External only — internal review bypassed | 0 |
| No approval yet | 6 |

## Pull requests

| PR | Author | State | Draft | → Ready | → 1st review | → Internal ✅ | → External ✅ | Handoff | Other ✅ | → Merge | CR | Rounds | Reviewers |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [pytorch/ao#4660](https://github.com/pytorch/ao/pull/4660) | karol-brejna-i | OPEN | 17.5h | 17.5h | 3d 21h | ⏳ 48d 8h | ⏳ 48d 8h | — | — | ⏳ 49d 2h | 0 | 0 | 1 |
| [pytorch/ao#4637](https://github.com/pytorch/ao/pull/4637) | karol-brejna-i | OPEN | 11.4h | 0m | 5d 12h | ⏳ 50d | ⏳ 50d | — | — | ⏳ 50d 12h | 0 | 0 | 1 |
| [pytorch/ao#4636](https://github.com/pytorch/ao/pull/4636) | karol-brejna-i | OPEN | 10.8h | 0m | 5d 13h | ⏳ 50d 1h | ⏳ 50d 1h | — | — | ⏳ 50d 12h | 0 | 0 | 1 |
| [pytorch/ao#4630](https://github.com/pytorch/ao/pull/4630) | karol-brejna-i | OPEN | 5d 17h | 5d 17h | 19.0h | ⏳ 45d 7h | ⏳ 45d 7h | — | — | ⏳ 51d 1h | 0 | 0 | 1 |
| [pytorch/ao#4629](https://github.com/pytorch/ao/pull/4629) | karol-brejna-i | OPEN | 5d 18h | 5d 18h | 19.0h | 37d 22h | ⏳ 45d 7h | ⏳ 7d 9h | — | ⏳ 51d 1h | 0 | 2 | 3 |
| [pytorch/ao#4628](https://github.com/pytorch/ao/pull/4628) | karol-brejna-i | OPEN | 5d 18h | 5d 18h | 18.9h | ⏳ 45d 7h | ⏳ 45d 7h | — | — | ⏳ 51d 1h | 0 | 0 | 1 |
| [pytorch/ao#4576](https://github.com/pytorch/ao/pull/4576) | karol-brejna-i | OPEN | 13.0h | 13.0h | ⏳ 62d 23h | ⏳ 62d 23h | ⏳ 62d 23h | — | — | ⏳ 63d 12h | 0 | 0 | 0 |

`—` = milestone not reached · `⏳` = still in flight · durations are ready hours (draft time excluded) except `→ Merge`, which is wall clock.

`Handoff` is internal sign-off → maintainer approval. It is blank unless our team approved first: a maintainer who approved without internal review never had a handoff to wait for.

## Attention

### Ready > 48h with no review

- [pytorch/ao#4576](https://github.com/pytorch/ao/pull/4576) — ready 62d 23h, no review yet

### Internally approved > 48h, waiting on a maintainer

- [pytorch/ao#4629](https://github.com/pytorch/ao/pull/4629) — internally approved 7d 9h ago, no maintainer approval
