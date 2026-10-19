"""The read-only GitHub reader against a fake HTTP transport (no network)."""

import httpx
import pytest

from app.adapters.github_live import GitHubReader, split_repo
from app.core.errors import InvalidToolOutput, ToolRateLimited, ToolTimeout, UnsupportedRepo


def commit(sha, msg, name="Ada"):
    full = sha if len(sha) == 40 else (sha * 40)[:40]
    return {"sha": full, "commit": {"message": msg, "author": {"name": name}}}


class FakeGitHub:
    """Routes by path prefix, longest first. `calls` records every request."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        for prefix in sorted(self.routes, key=len, reverse=True):
            if path == prefix or path.startswith(prefix):
                response = self.routes[prefix]
                return response(request) if callable(response) else response
        return httpx.Response(404, json={"message": "Not Found"})


def reader(fake: FakeGitHub, token: str = "") -> GitHubReader:
    return GitHubReader(token, http=httpx.Client(transport=httpx.MockTransport(fake)))


def standard():
    return FakeGitHub(
        {
            "/repos/octo/hello/compare/": httpx.Response(
                200,
                json={
                    "commits": [
                        commit("a", "fix: handle nil\n\nlong body"),
                        commit("b", "feat: add search", "Bo"),
                    ]
                },
            ),
            "/repos/octo/hello/tags": httpx.Response(
                200,
                json=[
                    {"name": "v1.2.0"},
                    {"name": "v1.10.0"},
                    {"name": "v1.9.9"},
                    {"name": "nightly"},
                ],
            ),
            "/repos/octo/hello/commits/main/check-runs": httpx.Response(
                200,
                json={
                    "check_runs": [
                        {"name": "ci", "conclusion": "success"},
                        {"name": "lint", "conclusion": "failure"},
                        {"name": "slow", "conclusion": None, "status": "in_progress"},
                    ]
                },
            ),
            "/repos/octo/hello/commits/main": httpx.Response(200, json={"sha": "c" * 40}),
            "/repos/octo/hello": httpx.Response(200, json={"default_branch": "main"}),
        }
    )


def test_snapshot_picks_the_highest_semver_tag_and_lists_commits_newest_first():
    snap = reader(standard()).get_repo_snapshot("octo/hello")
    assert snap["current_version"] == "1.10.0"  # numeric, not string, ordering
    assert snap["default_branch"] == "main" and snap["head_sha"] == "c" * 40
    assert [c["sha"] for c in snap["commits"]] == ["bbbbbbb", "aaaaaaa"]
    assert snap["commits"][1]["message"] == "fix: handle nil"  # first line only
    assert "nightly" in snap["tags"]


def test_no_semver_tag_uses_version_zero_and_recent_commits():
    fake = FakeGitHub(
        {
            "/repos/octo/new/tags": httpx.Response(200, json=[]),
            "/repos/octo/new/commits/main": httpx.Response(200, json={"sha": "d" * 40}),
            "/repos/octo/new/commits": httpx.Response(200, json=[commit("e", "init")]),
            "/repos/octo/new": httpx.Response(200, json={"default_branch": "main"}),
        }
    )
    snap = reader(fake).get_repo_snapshot("octo/new")
    assert snap["current_version"] == "0.0.0" and len(snap["commits"]) == 1


def test_commit_list_is_capped():
    many = [commit(f"{i:07x}", f"fix: {i}") for i in range(120)]
    fake = standard()
    fake.routes["/repos/octo/hello/compare/"] = httpx.Response(200, json={"commits": many})
    assert len(reader(fake).get_repo_snapshot("octo/hello")["commits"]) == 50


def test_checks_are_mapped_and_unfinished_ones_do_not_pass():
    results = reader(standard()).run_checks("octo/hello", "main")
    by = {r["name"]: r for r in results}
    assert by["ci"]["exit_code"] == 0 and by["lint"]["exit_code"] == 1
    assert by["slow"]["exit_code"] == 1 and by["slow"]["log_excerpt"] == "in_progress"


def test_commit_statuses_are_used_when_there_are_no_check_runs():
    fake = FakeGitHub(
        {
            "/repos/octo/hello/commits/abc/check-runs": httpx.Response(
                200, json={"check_runs": []}
            ),
            "/repos/octo/hello/commits/abc/status": httpx.Response(
                200, json={"statuses": [{"context": "travis", "state": "success"}]}
            ),
        }
    )
    assert reader(fake).run_checks("octo/hello", "abc")[0]["name"] == "travis"


def test_no_checks_at_all_is_stated_plainly_not_hidden():
    fake = FakeGitHub(
        {
            "/repos/octo/hello/commits/abc/check-runs": httpx.Response(
                200, json={"check_runs": []}
            ),
            "/repos/octo/hello/commits/abc/status": httpx.Response(200, json={"statuses": []}),
        }
    )
    only = reader(fake).run_checks("octo/hello", "abc")
    assert len(only) == 1 and only[0]["name"] == "no-checks-found"
    assert "nothing was verified" in only[0]["log_excerpt"]


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "a",
        "a/b/c",
        "../etc/passwd",
        "a/..",
        "a b/c",
        "-bad/x",
        "a/b?x=1",
        "a/b#frag",
        "o/" + "x" * 101,
        "http://evil/x",
        "a\\b/c",
    ],
)
def test_malformed_repository_names_are_rejected_before_any_request(bad):
    fake = standard()
    with pytest.raises(UnsupportedRepo):
        reader(fake).get_repo_snapshot(bad)
    assert fake.calls == []


def test_valid_names_pass():
    assert split_repo("psf/requests") == ("psf", "requests")
    assert split_repo("a-b/c.d_e") == ("a-b", "c.d_e")


def test_not_found_means_missing_or_private():
    with pytest.raises(UnsupportedRepo, match="private"):
        reader(FakeGitHub({})).get_repo_snapshot("octo/ghost")


def test_rate_limit_is_reported_with_a_short_wait():
    fake = FakeGitHub(
        {
            "/repos/octo/hello": httpx.Response(
                403,
                json={"message": "API rate limit exceeded"},
                headers={"x-ratelimit-remaining": "0"},
            )
        }
    )
    with pytest.raises(ToolRateLimited) as exc:
        reader(fake).get_repo_snapshot("octo/hello")
    assert exc.value.retry_after <= 5.0


def test_other_403_is_a_refusal_not_a_rate_limit():
    fake = FakeGitHub({"/repos/octo/hello": httpx.Response(403, json={"message": "Forbidden"})})
    with pytest.raises(UnsupportedRepo):
        reader(fake).get_repo_snapshot("octo/hello")


def test_timeouts_and_server_errors_are_retryable_failures():
    def timeout(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ToolTimeout):
        reader(FakeGitHub({"/repos": timeout})).get_repo_snapshot("octo/hello")
    with pytest.raises(ToolTimeout):
        reader(FakeGitHub({"/repos": httpx.Response(502)})).get_repo_snapshot("octo/hello")


def test_redirect_to_another_host_is_refused():
    fake = FakeGitHub(
        {
            "/repos/octo/hello": httpx.Response(
                301, headers={"location": "https://evil.example/steal"}
            )
        }
    )
    with pytest.raises(InvalidToolOutput):
        reader(fake).get_repo_snapshot("octo/hello")
    assert all(c.url.host == "api.github.com" for c in fake.calls)


def test_redirect_within_github_is_followed_once():
    fake = FakeGitHub(
        {
            "/repos/octo/old": httpx.Response(
                301, headers={"location": "https://api.github.com/repositories/42"}
            ),
            "/repositories/42": httpx.Response(200, json={"default_branch": "main"}),
            "/repos/octo/old/commits/main": httpx.Response(200, json={"sha": "f" * 40}),
        }
    )
    assert reader(fake).get_head_sha("octo/old") == "f" * 40


def test_token_is_sent_only_as_a_header_to_github_and_only_when_set():
    fake = standard()
    reader(fake, token="ghp_SECRET").get_repo_snapshot("octo/hello")
    assert all(c.headers["authorization"] == "Bearer ghp_SECRET" for c in fake.calls)
    assert all("ghp_SECRET" not in str(c.url) for c in fake.calls)
    anon = standard()
    reader(anon).get_repo_snapshot("octo/hello")
    assert all("authorization" not in c.headers for c in anon.calls)


def test_oversized_and_non_json_replies_are_rejected():
    big = FakeGitHub({"/repos/octo/hello": httpx.Response(200, content=b"x" * 2_000_001)})
    with pytest.raises(InvalidToolOutput):
        reader(big).get_repo_snapshot("octo/hello")
    junk = FakeGitHub({"/repos/octo/hello": httpx.Response(200, content=b"<html>")})
    with pytest.raises(InvalidToolOutput):
        reader(junk).get_repo_snapshot("octo/hello")
