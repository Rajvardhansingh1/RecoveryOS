"""A public repository with no commits yet (GitHub answers 409) gets a clear explanation."""

import httpx
import pytest
from test_api import make_client

from app.adapters.github_live import GitHubReader
from app.core.errors import InvalidToolOutput, NothingToRelease
from app.models.domain import RunStatus

EMPTY = httpx.Response(409, json={"message": "Git Repository is empty."})


def routes(handler):
    return GitHubReader(http=httpx.Client(transport=httpx.MockTransport(handler)))


def empty_repo(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/repos/me/fresh":
        return httpx.Response(200, json={"default_branch": "main"})
    return EMPTY  # commits, tags and compare all say the repository is empty


def test_an_empty_repository_means_nothing_to_release_not_a_bad_reply():
    with pytest.raises(NothingToRelease):
        routes(empty_repo).get_repo_snapshot("me/fresh")


def test_a_different_409_is_still_an_unexpected_reply():
    other = routes(lambda r: httpx.Response(409, json={"message": "Conflict"}))
    with pytest.raises(InvalidToolOutput):
        other.get_repo_snapshot("me/fresh")


def test_preview_of_an_empty_repository_says_to_push_first():
    from app.main import create_app  # noqa: F401  (import check only)

    client, _ = make_client()
    client.app.state.ctx.executor.adapter._reader = routes(empty_repo)
    resp = client.get("/v1/repos/me/fresh/preview")
    assert resp.status_code == 409
    body = resp.json()["error"]
    assert body["code"] == "repo_empty" and "Push your code first" in body["message"]


def test_a_run_on_an_empty_repository_ends_with_nothing_to_release():
    client, store = make_client()
    client.app.state.ctx.executor.adapter._reader = routes(empty_repo)
    run_id = client.post("/v1/runs", json={"repo": "me/fresh"}).json()["id"]
    detail = client.get(f"/v1/runs/{run_id}").json()
    assert detail["status"] == RunStatus.FAILED and detail["error_code"] == "nothing_to_release"
    assert detail["remote_effects"] == []
    assert client.get(f"/v1/runs/{run_id}/plan").status_code == 409  # no plan to download


def test_unexpected_replies_name_the_status_without_leaking_anything_else():
    client, _ = make_client()
    teapot = routes(lambda r: httpx.Response(418, json={"message": "I am a teapot"}))
    client.app.state.ctx.executor.adapter._reader = teapot
    resp = client.get("/v1/repos/me/odd/preview")
    assert resp.status_code == 502 and resp.json()["error"]["code"] == "github_bad_reply"
    assert (
        "418" in resp.json()["error"]["message"] and "teapot" not in resp.json()["error"]["message"]
    )
