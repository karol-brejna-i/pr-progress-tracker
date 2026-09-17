from __future__ import annotations

import json
import urllib.error

import pytest

from pr_tracker import github
from pr_tracker.contracts import PRRef
from pr_tracker.github import FetchError, HttpResponse, fetch_pull_request

REF = PRRef(owner="pytorch", repo="ao", number=4893)
TOKEN = "ghp_SUPERSECRET_do_not_leak_0123456789"


def envelope(
    *,
    nodes: list[dict] | None = None,
    has_next: bool = False,
    cursor: str | None = None,
    remaining: int = 4999,
    pull_request: dict | None = None,
) -> bytes:
    if pull_request is None:
        pull_request = {
            "id": "PR_1",
            "number": 4893,
            "title": "t",
            "state": "OPEN",
            "isDraft": False,
            "createdAt": "2026-01-01T00:00:00Z",
            "author": {"login": "alice"},
            "baseRefName": "main",
            "additions": 1,
            "deletions": 0,
            "changedFiles": 1,
            "timelineItems": {
                "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                "nodes": nodes or [],
            },
        }
    return json.dumps(
        {
            "data": {
                "rateLimit": {"cost": 1, "remaining": remaining, "resetAt": "2026-01-01T01:00:00Z"},
                "repository": {"pullRequest": pull_request},
            }
        }
    ).encode()


class RecordingTransport:
    """Returns queued responses in order, remembering every request it saw."""

    def __init__(self, responses: list[HttpResponse | Exception]):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def __call__(self, body: bytes, headers: dict[str, str]) -> HttpResponse:
        self.requests.append({"body": json.loads(body.decode()), "headers": headers})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def variables(self) -> list[dict]:
        return [r["body"]["variables"] for r in self.requests]


def ok(**kwargs) -> HttpResponse:
    return HttpResponse(status=200, body=envelope(**kwargs))


def no_sleep(_seconds: float) -> None:
    return None


class TestQueryConstant:
    def test_matches_design_4_1(self):
        q = github.QUERY
        assert "rateLimit { cost remaining resetAt }" in q
        assert "timelineItems(first:100, after:$cursor" in q
        assert "pageInfo { hasNextPage endCursor }" in q
        for typename in (
            "ReadyForReviewEvent",
            "ConvertToDraftEvent",
            "PullRequestReview",
            "ReviewRequestedEvent",
            "ReviewRequestRemovedEvent",
            "ReviewDismissedEvent",
            "MergedEvent",
            "ClosedEvent",
            "ReopenedEvent",
        ):
            assert typename in q
        # Design 4.1: do not request the `reviews` connection — two sources of truth.
        assert "reviews(" not in q
        assert "publishedAt" not in q


class TestHappyPath:
    def test_returns_unwrapped_pull_request(self):
        transport = RecordingTransport([ok(nodes=[{"__typename": "MergedEvent", "id": "ME_1"}])])
        payload = fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)
        assert payload["number"] == 4893
        assert "data" not in payload
        assert payload["timelineItems"]["nodes"] == [{"__typename": "MergedEvent", "id": "ME_1"}]

    def test_sends_query_and_variables(self):
        transport = RecordingTransport([ok()])
        fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)
        assert transport.variables == [
            {"owner": "pytorch", "repo": "ao", "number": 4893, "cursor": None}
        ]
        assert transport.requests[0]["body"]["query"] == github.QUERY

    def test_authorization_header_is_a_bearer_token(self):
        transport = RecordingTransport([ok()])
        fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)
        headers = transport.requests[0]["headers"]
        assert headers["Authorization"] == f"bearer {TOKEN}"
        assert headers["Content-Type"] == "application/json"


class TestRateLimit:
    def test_last_rate_limit_is_exposed(self):
        transport = RecordingTransport([ok(remaining=4321)])
        fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)
        seen = github.last_rate_limit()
        assert seen is not None
        assert seen["remaining"] == 4321
        assert seen["cost"] == 1
        assert seen["resetAt"] == "2026-01-01T01:00:00Z"

    def test_floor_constant_exists_for_early_abort(self):
        assert github.RATE_LIMIT_FLOOR == 100
        transport = RecordingTransport([ok(remaining=7)])
        fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)
        assert github.last_rate_limit()["remaining"] < github.RATE_LIMIT_FLOOR

    def test_rate_limit_not_injected_into_payload(self):
        transport = RecordingTransport([ok()])
        payload = fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)
        assert not [k for k in payload if "ratelimit" in k.lower()]


class TestPagination:
    def test_two_pages_are_concatenated(self):
        page1 = [
            {"__typename": "ReviewRequestedEvent", "id": "RRE_1"},
            {"__typename": "ReviewRequestedEvent", "id": "RRE_2"},
        ]
        page2 = [{"__typename": "MergedEvent", "id": "ME_1"}]
        transport = RecordingTransport(
            [
                ok(nodes=page1, has_next=True, cursor="CURSOR_A"),
                ok(nodes=page2, has_next=False, cursor="CURSOR_B"),
            ]
        )
        payload = fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)

        assert [n["id"] for n in payload["timelineItems"]["nodes"]] == ["RRE_1", "RRE_2", "ME_1"]
        assert payload["timelineItems"]["pageInfo"] == {
            "hasNextPage": False,
            "endCursor": "CURSOR_B",
        }
        assert [v["cursor"] for v in transport.variables] == [None, "CURSOR_A"]

    def test_three_pages(self):
        transport = RecordingTransport(
            [
                ok(nodes=[{"id": "a"}], has_next=True, cursor="c1"),
                ok(nodes=[{"id": "b"}], has_next=True, cursor="c2"),
                ok(nodes=[{"id": "c"}], has_next=False, cursor="c3"),
            ]
        )
        payload = fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)
        assert [n["id"] for n in payload["timelineItems"]["nodes"]] == ["a", "b", "c"]
        assert [v["cursor"] for v in transport.variables] == [None, "c1", "c2"]

    def test_single_page_makes_one_request(self):
        transport = RecordingTransport([ok(cursor="C")])
        fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)
        assert len(transport.requests) == 1

    def test_has_next_page_without_advancing_cursor_raises(self):
        transport = RecordingTransport([ok(has_next=True, cursor=None)])
        with pytest.raises(FetchError, match="pagination stalled"):
            fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)

    def test_empty_timeline(self):
        transport = RecordingTransport([ok(nodes=[])])
        payload = fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)
        assert payload["timelineItems"]["nodes"] == []


class TestErrors:
    def test_http_200_with_graphql_errors(self):
        body = json.dumps(
            {
                "data": {"repository": None},
                "errors": [{"message": "Could not resolve to a Repository with the name 'x/y'."}],
            }
        ).encode()
        transport = RecordingTransport([HttpResponse(status=200, body=body)])
        with pytest.raises(FetchError, match="Could not resolve to a Repository"):
            fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)

    def test_graphql_errors_are_joined(self):
        body = json.dumps({"errors": [{"message": "one"}, {"message": "two"}]}).encode()
        transport = RecordingTransport([HttpResponse(status=200, body=body)])
        with pytest.raises(FetchError, match="one; two"):
            fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)

    def test_null_pull_request(self):
        body = json.dumps({"data": {"repository": {"pullRequest": None}}}).encode()
        transport = RecordingTransport([HttpResponse(status=200, body=body)])
        with pytest.raises(FetchError, match="not accessible"):
            fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)

    def test_null_repository(self):
        body = json.dumps({"data": {"repository": None}}).encode()
        transport = RecordingTransport([HttpResponse(status=200, body=body)])
        with pytest.raises(FetchError, match="not accessible"):
            fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)

    def test_missing_data_object(self):
        transport = RecordingTransport([HttpResponse(status=200, body=b"{}")])
        with pytest.raises(FetchError, match="no `data` object"):
            fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)

    def test_malformed_json(self):
        transport = RecordingTransport([HttpResponse(status=200, body=b"<html>nope</html>")])
        with pytest.raises(FetchError, match="malformed JSON"):
            fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)

    def test_json_that_is_not_an_object(self):
        transport = RecordingTransport([HttpResponse(status=200, body=b"[1, 2, 3]")])
        with pytest.raises(FetchError, match="not an object"):
            fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)

    @pytest.mark.parametrize("status", [400, 401, 404, 422])
    def test_non_retryable_http_error_fails_immediately(self, status):
        transport = RecordingTransport([HttpResponse(status=status, body=b'{"message":"Bad"}')])
        with pytest.raises(FetchError, match=f"HTTP {status}"):
            fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)
        assert len(transport.requests) == 1

    def test_error_on_second_page_propagates(self):
        transport = RecordingTransport(
            [
                ok(nodes=[{"id": "a"}], has_next=True, cursor="c1"),
                HttpResponse(status=401, body=b'{"message":"Bad credentials"}'),
            ]
        )
        with pytest.raises(FetchError, match="HTTP 401"):
            fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)


class TestRetries:
    def test_retries_5xx_then_succeeds(self):
        sleeps: list[float] = []
        transport = RecordingTransport(
            [
                HttpResponse(status=502, body=b"bad gateway"),
                HttpResponse(status=503, body=b"unavailable"),
                ok(),
            ]
        )
        payload = fetch_pull_request(REF, TOKEN, transport=transport, sleep=sleeps.append)
        assert payload["number"] == 4893
        assert len(transport.requests) == 3
        assert sleeps == [1.0, 2.0]  # exponential

    def test_retries_429_and_403(self):
        sleeps: list[float] = []
        transport = RecordingTransport(
            [
                HttpResponse(status=429, body=b"slow down"),
                HttpResponse(status=403, body=b"secondary rate limit"),
                ok(),
            ]
        )
        fetch_pull_request(REF, TOKEN, transport=transport, sleep=sleeps.append)
        assert len(transport.requests) == 3
        assert len(sleeps) == 2

    def test_retry_after_seconds_header_is_honoured(self):
        sleeps: list[float] = []
        transport = RecordingTransport(
            [
                HttpResponse(status=429, body=b"", headers={"Retry-After": "17"}),
                ok(),
            ]
        )
        fetch_pull_request(REF, TOKEN, transport=transport, sleep=sleeps.append)
        assert sleeps == [17.0]

    def test_retry_after_is_case_insensitive(self):
        sleeps: list[float] = []
        transport = RecordingTransport(
            [
                HttpResponse(status=503, body=b"", headers={"retry-after": "5"}),
                ok(),
            ]
        )
        fetch_pull_request(REF, TOKEN, transport=transport, sleep=sleeps.append)
        assert sleeps == [5.0]

    def test_retry_after_is_capped(self):
        sleeps: list[float] = []
        transport = RecordingTransport(
            [
                HttpResponse(status=503, body=b"", headers={"Retry-After": "99999"}),
                ok(),
            ]
        )
        fetch_pull_request(REF, TOKEN, transport=transport, sleep=sleeps.append)
        assert sleeps == [float(github.MAX_BACKOFF_SECONDS)]

    def test_attempts_are_capped(self):
        sleeps: list[float] = []
        transport = RecordingTransport(
            [HttpResponse(status=500, body=b"boom")] * github.MAX_ATTEMPTS
        )
        with pytest.raises(FetchError, match=f"giving up after {github.MAX_ATTEMPTS} attempts"):
            fetch_pull_request(REF, TOKEN, transport=transport, sleep=sleeps.append)
        assert len(transport.requests) == github.MAX_ATTEMPTS
        # No sleep after the final failed attempt.
        assert len(sleeps) == github.MAX_ATTEMPTS - 1

    def test_network_error_is_retried(self):
        sleeps: list[float] = []
        transport = RecordingTransport([urllib.error.URLError("connection reset"), ok()])
        fetch_pull_request(REF, TOKEN, transport=transport, sleep=sleeps.append)
        assert len(transport.requests) == 2
        assert sleeps == [1.0]

    def test_persistent_network_error_raises(self):
        transport = RecordingTransport([urllib.error.URLError("dns failure")] * github.MAX_ATTEMPTS)
        with pytest.raises(FetchError, match="network error"):
            fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)

    def test_tests_never_really_sleep(self, monkeypatch):
        called: list[float] = []

        def boom(seconds: float) -> None:
            called.append(seconds)
            raise AssertionError("time.sleep must not be called with an injected sleep")

        monkeypatch.setattr("time.sleep", boom)
        transport = RecordingTransport([HttpResponse(status=500, body=b"x"), ok()])
        fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)
        assert called == []


class TestTokenNeverLeaks:
    @pytest.mark.parametrize(
        "responses",
        [
            [HttpResponse(status=401, body=b'{"message":"Bad credentials"}')],
            [HttpResponse(status=200, body=b"not json")],
            [HttpResponse(status=200, body=json.dumps({"errors": [{"message": "nope"}]}).encode())],
            [HttpResponse(status=500, body=b"boom")] * 5,
            [urllib.error.URLError("refused")] * 5,
        ],
    )
    def test_token_absent_from_raised_message(self, responses):
        transport = RecordingTransport(responses)
        with pytest.raises(FetchError) as excinfo:
            fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)
        rendered = f"{excinfo.value!r} {excinfo.value}"
        assert TOKEN not in rendered
        assert "SUPERSECRET" not in rendered

    def test_token_echoed_by_the_server_is_scrubbed(self):
        # A server that reflects the credential must not turn our error into a leak.
        body = json.dumps({"errors": [{"message": f"bad token {TOKEN}"}]}).encode()
        transport = RecordingTransport([HttpResponse(status=200, body=body)])
        with pytest.raises(FetchError) as excinfo:
            fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)
        assert TOKEN not in str(excinfo.value)
        assert "***" in str(excinfo.value)

    def test_token_echoed_in_http_error_body_is_scrubbed(self):
        transport = RecordingTransport(
            [HttpResponse(status=401, body=f"token {TOKEN} rejected".encode())]
        )
        with pytest.raises(FetchError) as excinfo:
            fetch_pull_request(REF, TOKEN, transport=transport, sleep=no_sleep)
        assert TOKEN not in str(excinfo.value)


class TestUrllibTransportIsMockable:
    """The default transport path, exercised without a socket."""

    def test_monkeypatched_urlopen(self, monkeypatch):
        captured: dict = {}

        class FakeResponse:
            status = 200
            headers = {"X-Test": "1"}

            def read(self):
                return envelope(nodes=[{"__typename": "ClosedEvent", "id": "CE_1"}])

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, *args, **kwargs):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["headers"] = dict(request.header_items())
            return FakeResponse()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        payload = fetch_pull_request(REF, TOKEN, sleep=no_sleep)

        assert captured["url"] == github.API_URL
        assert captured["method"] == "POST"
        assert payload["timelineItems"]["nodes"][0]["id"] == "CE_1"

    def test_http_error_becomes_fetch_error(self, monkeypatch):
        def fake_urlopen(request, *args, **kwargs):
            raise urllib.error.HTTPError(
                github.API_URL,
                401,
                "Unauthorized",
                {},
                None,  # type: ignore[arg-type]
            )

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(FetchError, match="HTTP 401"):
            fetch_pull_request(REF, TOKEN, sleep=no_sleep)
