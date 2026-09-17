# CLAUDE.md

**Read [AGENTS.md](AGENTS.md) first** — it is the canonical set of working agreements for
this repo (toolchain, subagent roster and when to use each, the never-delegate command
list, and the contracts seam that makes parallel module work safe).

**[docs/design.md](docs/design.md) is the specification** for the service. Read the section
covering your task before writing code.

Two things that are easy to get wrong and expensive to rediscover:

- Runtime code is **Python 3.12 stdlib only**. No runtime dependencies.
- `python -m pr_tracker track` hits the **live GitHub API with a real token**. Never run it
  as a casual verification step, and never inside a subagent.
