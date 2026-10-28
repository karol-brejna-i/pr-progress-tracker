# PR progress

Generated 2026-10-28T20:14:51Z · 12 tracked PRs · 10 open (2 draft) · 1 merged · 1 closed

## Aggregates

Medians and percentiles over the PRs whose milestone completed; `n` is how many
of the tracked PRs that is.

| Metric | n / 12 | median | p90 | max |
| --- | --- | --- | --- | --- |
| Draft total | 12 | 5d 18h | 75d 15h | 92d 16h |
| Created → ready | 12 | 6.5h | 5d 18h | 92d 16h |
| Ready → first review | 9 | 19.0h | 7d | 12d 21h |
| Ready → internal review | 4 | 6d 11h | 30d 5h | 37d 15h |
| Ready → external review | 4 | 7d 2h | 35d 17h | 45d |
| Ready → internal approval | 4 | 6d 19h | 30d 15h | 37d 22h |
| Ready → external approval | 1 | 11d 23h | 11d 23h | 11d 23h |
| Internal → external approval (handoff) | 1 | 11d 22h | 11d 22h | 11d 22h |
| Created → merge (wall) | 1 | 11d 23h | 11d 23h | 11d 23h |
| Open duration | 12 | 91d 19h | 112d 8h | 138d 14h |

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
| [pytorch/ao#4843](https://github.com/pytorch/ao/pull/4843) | xiaowangintel | OPEN (draft) | ⏳ 61d 10h | 0m | ⏳ 8.0h | ⏳ 8.0h | ⏳ 8.0h | — | — | ⏳ 61d 18h | 0 | 0 | 0 |
| [pytorch/ao#4731](https://github.com/pytorch/ao/pull/4731) | eryk-roch | CLOSED (draft) | ⏳ 36d 11h | 0m | — | — | — | — | — | — | 0 | 0 | 0 |
| [pytorch/ao#4660](https://github.com/pytorch/ao/pull/4660) | karol-brejna-i | OPEN | 17.5h | 17.5h | 3d 21h | ⏳ 89d 10h | ⏳ 89d 10h | — | — | ⏳ 90d 3h | 0 | 0 | 1 |
| [pytorch/ao#4637](https://github.com/pytorch/ao/pull/4637) | karol-brejna-i | OPEN | 11.4h | 0m | 5d 12h | ⏳ 91d 1h | ⏳ 91d 1h | — | — | ⏳ 91d 13h | 0 | 0 | 1 |
| [pytorch/ao#4636](https://github.com/pytorch/ao/pull/4636) | karol-brejna-i | OPEN | 10.8h | 0m | 5d 13h | ⏳ 91d 2h | ⏳ 91d 2h | — | — | ⏳ 91d 13h | 0 | 0 | 1 |
| [pytorch/ao#4630](https://github.com/pytorch/ao/pull/4630) | karol-brejna-i | OPEN | 5d 17h | 5d 17h | 19.0h | ⏳ 86d 8h | ⏳ 86d 8h | — | — | ⏳ 92d 2h | 0 | 0 | 1 |
| [pytorch/ao#4629](https://github.com/pytorch/ao/pull/4629) | karol-brejna-i | OPEN | 5d 18h | 5d 18h | 19.0h | 37d 22h | ⏳ 86d 8h | ⏳ 48d 10h | — | ⏳ 92d 3h | 0 | 2 | 3 |
| [pytorch/ao#4628](https://github.com/pytorch/ao/pull/4628) | karol-brejna-i | OPEN | 5d 18h | 5d 18h | 18.9h | ⏳ 86d 8h | ⏳ 86d 8h | — | — | ⏳ 92d 3h | 0 | 0 | 1 |
| [pytorch/ao#4576](https://github.com/pytorch/ao/pull/4576) | karol-brejna-i | OPEN | 13.0h | 13.0h | ⏳ 104d | ⏳ 104d | ⏳ 104d | — | — | ⏳ 104d 13h | 0 | 0 | 0 |
| [pytorch/ao#4560](https://github.com/pytorch/ao/pull/4560) | draghan | OPEN | 92d 16h | 92d 16h | 0m | 0m | ⏳ 20d 12h | ⏳ 20d 12h | — | ⏳ 113d 5h | 0 | 1 | 2 |
| [pytorch/ao#4477](https://github.com/pytorch/ao/pull/4477) | xiaowangintel | OPEN | 77d 5h | 0m | 12d 21h | 13d 13h | ⏳ 61d 9h | ⏳ 47d 20h | — | ⏳ 138d 14h | 0 | 1 | 2 |

`—` = milestone not reached · `⏳` = still in flight · durations are ready hours (draft time excluded) except `→ Merge`, which is wall clock.

`Handoff` is internal sign-off → maintainer approval. It is blank unless our team approved first: a maintainer who approved without internal review never had a handoff to wait for.

## Attention

### Ready > 48h with no review

- [pytorch/ao#4576](https://github.com/pytorch/ao/pull/4576) — ready 104d, no review yet

### Internally approved > 48h, waiting on a maintainer

- [pytorch/ao#4629](https://github.com/pytorch/ao/pull/4629) — internally approved 48d 10h ago, no maintainer approval
- [pytorch/ao#4560](https://github.com/pytorch/ao/pull/4560) — internally approved 20d 12h ago, no maintainer approval
- [pytorch/ao#4477](https://github.com/pytorch/ao/pull/4477) — internally approved 47d 20h ago, no maintainer approval
