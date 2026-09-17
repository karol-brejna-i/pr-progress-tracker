"""GitHub GraphQL fetch layer. Standard library only (see docs/design.md 4.2).

Public surface:

    QUERY                  the canonical GraphQL document of design 4.1
    RATE_LIMIT_FLOOR       stop-the-run threshold for `rateLimit.remaining`
    MAX_ATTEMPTS           retry cap per HTTP request
    FetchError             one-line human-readable failure
    HttpResponse           what a transport returns
    fetch_pull_request()   the only entry point
    last_rate_limit()      most recent `rateLimit` block seen, or None

Rate limit exposure: `fetch_pull_request` does NOT decorate the returned payload. The
caller reads `last_rate_limit()` after each fetch and compares `remaining` against
`RATE_LIMIT_FLOOR` to abort the run early (design 4.2: report partial success rather than
hammer the API). Keeping it off the payload keeps the returned object a faithful
`pullRequest` object that `normalize` and `store` can consume without filtering.

The token is used only to build the `Authorization` header. It never reaches a log line,
an exception message, or the returned dict; `_scrub` is the belt to that braces.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from pr_tracker.contracts import PRRef

API_URL = "https://api.github.com/graphql"

# Abort the run when `rateLimit.remaining` drops below this (design 4.2).
RATE_LIMIT_FLOOR = 100

MAX_ATTEMPTS = 5
BASE_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 60.0

# A PR with >10k timeline items would be pathological; the cap turns a server-side
# pagination bug into a clean error instead of an unbounded loop.
MAX_PAGES = 100

RETRY_STATUSES = frozenset({403, 429, 500, 502, 503, 504})

# Verbatim from docs/design.md 4.1. `rateLimit` is part of the query on purpose: the cost
# accounting has to come from the same round trip the data did.
QUERY = """
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
"""


class FetchError(Exception):
    """A fetch that cannot be retried into success. Message is one human-readable line."""


@dataclass(frozen=True)
class HttpResponse:
    """Minimal transport result, so tests can hand back canned responses."""

    status: int
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)

    def header(self, name: str) -> str | None:
        """Case-insensitive header lookup."""
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return None


# (body, headers) -> HttpResponse. Raising urllib.error.URLError signals a retryable
# network failure; HTTP error *statuses* come back as a normal HttpResponse.
Transport = Callable[[bytes, dict[str, str]], HttpResponse]

_last_rate_limit: dict[str, Any] | None = None


def last_rate_limit() -> dict[str, Any] | None:
    """The `rateLimit` block from the most recent successful response, or None."""
    return _last_rate_limit


def _scrub(text: str, token: str) -> str:
    """Defence in depth: never let a credential ride out on an error message."""
    if token and token in text:
        text = text.replace(token, "***")
    return text


def _urllib_transport(body: bytes, headers: dict[str, str]) -> HttpResponse:
    request = urllib.request.Request(API_URL, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request) as response:  # noqa: S310 - fixed https URL
            return HttpResponse(
                status=response.status,
                body=response.read(),
                headers=dict(response.headers.items()),
            )
    except urllib.error.HTTPError as exc:
        # An HTTPError still carries a body and headers; treat it as a response so the
        # retry policy (and GraphQL `errors` parsing) sees it uniformly.
        return HttpResponse(
            status=exc.code,
            body=(exc.read() or b"") if exc.fp is not None else b"",
            headers=dict(exc.headers.items()) if exc.headers else {},
        )


def _retry_after_seconds(response: HttpResponse) -> float | None:
    """`Retry-After` as seconds. GitHub sends an integer; HTTP allows a date."""
    raw = response.header("Retry-After")
    if not raw:
        return None
    raw = raw.strip()
    try:
        return max(0.0, float(int(raw)))
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    return max(0.0, (target - datetime.now(UTC)).total_seconds())


def _backoff_seconds(attempt: int, response: HttpResponse | None) -> float:
    """Exponential backoff, overridden by `Retry-After` when the server sent one."""
    if response is not None:
        hinted = _retry_after_seconds(response)
        if hinted is not None:
            return min(hinted, MAX_BACKOFF_SECONDS)
    return min(BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)


def _post(
    variables: dict[str, Any],
    token: str,
    transport: Transport,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    """One GraphQL round trip with retries. Returns the decoded response envelope."""
    body = json.dumps({"query": QUERY, "variables": variables}).encode("utf-8")
    headers = {
        "Authorization": f"bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "pr-progress-tracker",
    }

    last_reason = "no attempt was made"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response: HttpResponse | None = None
        try:
            response = transport(body, dict(headers))
        except urllib.error.URLError as exc:
            last_reason = f"network error contacting the GitHub API: {exc.reason}"
        else:
            if response.status not in RETRY_STATUSES:
                if response.status != 200:
                    raise FetchError(
                        _scrub(
                            f"GitHub API returned HTTP {response.status}: "
                            f"{_short_body(response.body)}",
                            token,
                        )
                    )
                return _decode(response.body, token)
            last_reason = (
                f"GitHub API returned HTTP {response.status}: {_short_body(response.body)}"
            )

        if attempt < MAX_ATTEMPTS:
            sleep(_backoff_seconds(attempt, response))

    raise FetchError(_scrub(f"giving up after {MAX_ATTEMPTS} attempts: {last_reason}", token))


def _short_body(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace").strip().replace("\n", " ")
    return text[:200] if text else "(empty body)"


def _decode(body: bytes, token: str) -> dict[str, Any]:
    try:
        envelope = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(_scrub(f"GitHub API returned malformed JSON: {exc}", token)) from exc
    if not isinstance(envelope, dict):
        raise FetchError("GitHub API returned JSON that is not an object")
    return envelope


def _graphql_error_message(envelope: Mapping[str, Any]) -> str | None:
    """GraphQL failures arrive as HTTP 200 with a top-level `errors` array."""
    errors = envelope.get("errors")
    if not errors:
        return None
    if isinstance(errors, list):
        messages = []
        for item in errors:
            if isinstance(item, Mapping):
                messages.append(str(item.get("message") or item))
            else:
                messages.append(str(item))
        joined = "; ".join(m for m in messages if m)
        return joined or "unspecified GraphQL error"
    return str(errors)


def fetch_pull_request(
    ref: PRRef,
    token: str,
    *,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Fetch one PR, following every `timelineItems` page.

    Returns the unwrapped `data.repository.pullRequest` object with all pages'
    `timelineItems.nodes` concatenated into a single list. `transport` and `sleep` are
    injection points for tests; production uses `urllib.request` and `time.sleep`.
    """
    global _last_rate_limit

    transport = transport or _urllib_transport
    cursor: str | None = None
    merged: dict[str, Any] | None = None
    nodes: list[Any] = []
    last_page_info: dict[str, Any] = {"hasNextPage": False, "endCursor": None}

    for page in range(1, MAX_PAGES + 1):
        envelope = _post(
            {
                "owner": ref.owner,
                "repo": ref.repo,
                "number": ref.number,
                "cursor": cursor,
            },
            token,
            transport,
            sleep,
        )

        message = _graphql_error_message(envelope)
        if message:
            raise FetchError(_scrub(f"GraphQL error for {ref.key}: {message}", token))

        data = envelope.get("data")
        if not isinstance(data, Mapping):
            raise FetchError(f"GitHub API response for {ref.key} had no `data` object")

        rate_limit = data.get("rateLimit")
        if isinstance(rate_limit, Mapping):
            _last_rate_limit = dict(rate_limit)

        repository = data.get("repository")
        pull_request = repository.get("pullRequest") if isinstance(repository, Mapping) else None
        if not isinstance(pull_request, Mapping):
            raise FetchError(
                f"{ref.key} is not accessible: the API returned no pull request "
                "(deleted, renamed, private, or the token lacks access)"
            )

        timeline = pull_request.get("timelineItems")
        timeline = timeline if isinstance(timeline, Mapping) else {}
        page_nodes = timeline.get("nodes")
        nodes.extend(page_nodes if isinstance(page_nodes, list) else [])

        page_info = timeline.get("pageInfo")
        page_info = dict(page_info) if isinstance(page_info, Mapping) else {}
        last_page_info = {
            "hasNextPage": bool(page_info.get("hasNextPage")),
            "endCursor": page_info.get("endCursor"),
        }

        if merged is None:
            merged = dict(pull_request)

        if not page_info.get("hasNextPage"):
            break
        next_cursor = page_info.get("endCursor")
        if not next_cursor or next_cursor == cursor:
            # hasNextPage without a usable cursor would loop forever.
            raise FetchError(
                f"{ref.key} timeline pagination stalled at page {page}: "
                "hasNextPage was true but endCursor did not advance"
            )
        cursor = next_cursor
    else:
        raise FetchError(f"{ref.key} timeline exceeded {MAX_PAGES} pages; refusing to continue")

    assert merged is not None  # loop body runs at least once or raises
    merged["timelineItems"] = {"pageInfo": last_page_info, "nodes": nodes}
    return merged
