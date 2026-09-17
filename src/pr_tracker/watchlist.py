"""Loading and validating committed configuration. Owned by the main session.

Config errors fail the whole run rather than being skipped. A typo'd URL that silently
drops a PR from the watchlist is worse than a red build: the report still looks healthy
while quietly tracking less than it claims.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pr_tracker.contracts import Config, PRRef

CONFIG_DIR = Path("config")

_PR_URL = re.compile(
    r"^https://github\.com/"
    r"(?P<owner>[A-Za-z0-9][A-Za-z0-9._-]*)/"
    r"(?P<repo>[A-Za-z0-9][A-Za-z0-9._-]*)/"
    r"pull/(?P<number>\d+)"
    r"(?:/[A-Za-z0-9._/-]*)?$"  # tolerate pasted /files, /commits, etc.
)


class ConfigError(Exception):
    """Malformed configuration. Always fatal."""


def parse_pr_url(url: str) -> PRRef:
    """Parse a full PR URL into a PRRef. Raises ConfigError on anything unrecognized."""
    cleaned = url.strip().split("#", 1)[0].split("?", 1)[0]
    match = _PR_URL.match(cleaned)
    if not match:
        raise ConfigError(
            f"not a recognizable PR URL: {url!r} "
            "(expected https://github.com/<owner>/<repo>/pull/<number>)"
        )
    return PRRef(
        owner=match["owner"],
        repo=match["repo"],
        number=int(match["number"]),
    )


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def load_watchlist(path: Path | None = None) -> list[PRRef]:
    """Parse config/prs.txt. Duplicates are collapsed, order of first appearance kept."""
    path = path or CONFIG_DIR / "prs.txt"
    seen: dict[str, PRRef] = {}
    for line in _read_lines(path):
        ref = parse_pr_url(line)
        seen.setdefault(ref.key, ref)
    return list(seen.values())


def _load_logins(path: Path) -> frozenset[str]:
    return frozenset(line.lstrip("@").lower() for line in _read_lines(path))


def load_config(config_dir: Path | None = None) -> Config:
    """Load reviewer lists and optional settings.json."""
    config_dir = config_dir or CONFIG_DIR
    internal = _load_logins(config_dir / "internal-reviewers.txt")
    external = _load_logins(config_dir / "external-reviewers.txt")

    both = internal & external
    if both:
        raise ConfigError(
            "these logins appear in BOTH internal-reviewers.txt and "
            f"external-reviewers.txt: {', '.join(sorted(both))}"
        )

    settings: dict = {}
    settings_path = config_dir / "settings.json"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{settings_path} is not valid JSON: {exc}") from exc

    return Config(
        internal_reviewers=internal,
        external_reviewers=external,
        stale_review_hours=int(settings.get("stale_review_hours", 48)),
        stale_merge_hours=int(settings.get("stale_merge_hours", 72)),
        percentiles=tuple(settings.get("percentiles", (50, 90))),
    )
