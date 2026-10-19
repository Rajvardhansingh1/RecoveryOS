import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from helpers import ScriptedLLM, good_replies

from app.adapters.github import _FIXTURES
from app.adapters.github_live import GitHubReader
from app.config import Settings
from app.db.store import Store, make_engine
from app.main import create_app


def make_client(**overrides):
    settings = Settings(_env_file=None, demo_mode=True, retry_backoff_seconds=0, **overrides)
    store = Store(make_engine("sqlite+pysqlite:///:memory:"))
    store.create_schema()
    llm = ScriptedLLM(good_replies(_FIXTURES["demo/acme-api"]["commits"]))
    offline = GitHubReader(
        http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    )
    app = create_app(settings, store=store, reader=offline, llm_factory=lambda cap: llm)
    return TestClient(app), store


def test_health_reports_database_and_flags():
    client, _ = make_client()
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["database"] == "ok"
    assert body["real_github_writes"] is False


def test_create_run_waits_for_approval_then_approve_completes():
    client, _ = make_client()
    created = client.post("/v1/runs", json={"repo": "demo/acme-api"})
    assert created.status_code == 202
    run_id = created.json()["id"]

    detail = client.get(f"/v1/runs/{run_id}").json()
    assert detail["status"] == "awaiting_approval"
    assert detail["llm_steps"] == 5 and detail["fallback_steps"] == 0
    assert {s["produced_by"] for s in detail["steps"]} == {"llm"}
    assert detail["approval"]["status"] == "pending"
    assert not any(e["action"] == "publish" for e in detail["remote_effects"])

    done = client.post(f"/v1/runs/{run_id}/approve", json={"by": "alice"}).json()
    assert done["status"] == "completed"
    assert done["steps"][-1]["name"] == "publish_release"
    timeline = client.get(f"/v1/runs/{run_id}/timeline").json()
    assert timeline[0]["event"] == "run_created" and timeline[-1]["event"] == "run_completed"


def test_crash_then_resume_over_http():
    client, _ = make_client()
    run_id = client.post(
        "/v1/runs",
        json={
            "repo": "demo/acme-api",
            "faults": [{"step": "run_checks", "kind": "crash_after_step"}],
        },
    ).json()["id"]
    detail = client.get(f"/v1/runs/{run_id}").json()
    assert detail["interrupted"] is True and detail["status"] == "running"
    assert client.post(f"/v1/runs/{run_id}/resume").status_code == 202
    detail = client.get(f"/v1/runs/{run_id}").json()
    assert detail["status"] == "awaiting_approval" and detail["interrupted"] is False


def test_approve_when_not_waiting_is_refused():
    client, store = make_client()
    run_id = client.post("/v1/runs", json={"repo": "demo/failing-checks"}).json()["id"]
    resp = client.post(f"/v1/runs/{run_id}/approve", json={})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "not_awaiting_approval"


def test_reject_ends_run():
    client, _ = make_client()
    run_id = client.post("/v1/runs", json={"repo": "demo/acme-api"}).json()["id"]
    assert client.post(f"/v1/runs/{run_id}/reject", json={}).json()["status"] == "rejected"


@pytest.mark.parametrize(
    "body",
    [
        {"repo": "not a repo"},
        {"repo": "../../etc/passwd"},
        {},
        {"repo": "a/b", "extra": 1},
        {"repo": "demo/acme-api", "faults": [{"step": "nope", "kind": "timeout"}]},
    ],
)
def test_invalid_requests_get_a_fixed_safe_error(body):
    client, _ = make_client()
    resp = client.post("/v1/runs", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] in ("invalid_request",)
    assert "passwd" not in resp.text


def test_unknown_public_repo_is_accepted_and_ends_unsupported_when_github_says_404():
    client, _ = make_client()
    resp = client.post("/v1/runs", json={"repo": "someone/else"})
    assert resp.status_code == 202 and resp.json()["source"] == "github"
    detail = client.get(f"/v1/runs/{resp.json()['id']}").json()
    assert detail["status"] == "failed" and detail["error_code"] == "unsupported_repo"
    assert detail["writes"] == "simulated"


def test_linking_can_be_turned_off():
    settings = Settings(_env_file=None, enable_github_link=False)
    store = Store(make_engine("sqlite+pysqlite:///:memory:"))
    store.create_schema()
    client = TestClient(create_app(settings, store=store, llm_factory=lambda cap: None))
    resp = client.post("/v1/runs", json={"repo": "someone/else"})
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "github_link_disabled"
    assert client.get("/v1/repos").json()["github_link"] is False
    assert client.post("/v1/runs", json={"repo": "demo/acme-api"}).status_code == 202


def test_demo_repo_preview_shows_what_the_agent_will_read():
    client, _ = make_client()
    body = client.get("/v1/repos/demo/acme-api/preview").json()
    assert body["source"] == "demo" and body["writes"] == "simulated"
    assert body["current_version"] == "1.4.2" and len(body["commits"]) == 4
    assert body["checks"][0]["name"] == "unit-tests"
    assert "v1.4.2" in body["tags"]


def test_preview_of_a_missing_github_repo_explains_public_only():
    client, _ = make_client()
    resp = client.get("/v1/repos/someone/else/preview")
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "repo_not_found"
    assert "public" in resp.json()["error"]["message"]


def test_preview_rejects_malformed_names_and_respects_the_off_switch():
    client, _ = make_client()
    assert client.get("/v1/repos/-bad/x/preview").status_code == 422
    settings = Settings(_env_file=None, enable_github_link=False)
    store = Store(make_engine("sqlite+pysqlite:///:memory:"))
    store.create_schema()
    off = TestClient(create_app(settings, store=store, llm_factory=lambda cap: None))
    assert off.get("/v1/repos/someone/else/preview").status_code == 403
    assert off.get("/v1/repos/demo/acme-api/preview").status_code == 200  # demo always works


def test_faults_are_refused_outside_demo_mode():
    settings = Settings(_env_file=None, demo_mode=False)
    store = Store(make_engine("sqlite+pysqlite:///:memory:"))
    store.create_schema()
    client = TestClient(create_app(settings, store=store, llm_factory=lambda cap: None))
    resp = client.post(
        "/v1/runs",
        json={"repo": "demo/acme-api", "faults": [{"step": "inspect_repo", "kind": "timeout"}]},
    )
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "faults_disabled"


def test_unknown_run_is_404():
    client, _ = make_client()
    resp = client.get("/v1/runs/does-not-exist")
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "run_not_found"


def test_oversized_body_is_rejected_before_parsing():
    client, _ = make_client(max_request_bytes=100)
    resp = client.post("/v1/runs", content=b"x" * 500, headers={"Content-Type": "application/json"})
    assert resp.status_code == 413 and resp.json()["error"]["code"] == "request_too_large"


def test_rate_limit_returns_429_with_retry_after():
    client, _ = make_client(rate_limit_per_minute=3)
    codes = [client.get("/v1/repos").status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
    assert client.get("/health").status_code == 200  # health is exempt


def test_cors_allows_only_configured_origin():
    client, _ = make_client(cors_origins="https://app.example.com")
    ok = client.get("/v1/repos", headers={"Origin": "https://app.example.com"})
    bad = client.get("/v1/repos", headers={"Origin": "https://evil.example.com"})
    assert ok.headers.get("access-control-allow-origin") == "https://app.example.com"
    assert "access-control-allow-origin" not in bad.headers


def test_secrets_and_payloads_never_reach_logs(caplog):
    client, _ = make_client(openrouter_api_key="sk-or-SUPERSECRET")
    with caplog.at_level(logging.INFO, logger="recoveryos"):
        client.post("/v1/runs", json={"repo": "demo/acme-api"})
        client.get("/health")
    text = caplog.text
    assert "SUPERSECRET" not in text
    assert "demo/acme-api" not in text  # route template is logged, not the body
    assert "req=" in text


def test_run_that_finishes_during_the_request_is_not_reported_as_interrupted():
    """Regression: the status was read, the worker then finished and released the run, and the
    later 'is anyone executing?' check made a healthy run look crashed (the UI stopped polling)."""
    client, store = make_client()
    run_id = client.post("/v1/runs", json={"repo": "demo/acme-api"}).json()["id"]
    ctx = client.app.state.ctx
    real_get_run = store.get_run

    def stale_running(rid):
        row = real_get_run(rid)
        return {**row, "status": "running"}  # what a read just before completion would see

    answers = iter([True])  # a worker held the run when the request began, then released it
    ctx.active.is_active = lambda rid: next(answers, False)
    store.get_run = stale_running
    detail = client.get(f"/v1/runs/{run_id}").json()
    assert detail["status"] == "running" and detail["interrupted"] is False


def test_a_run_nobody_is_executing_is_reported_as_interrupted():
    client, store = make_client()
    run_id = client.post(
        "/v1/runs",
        json={
            "repo": "demo/acme-api",
            "faults": [{"step": "run_checks", "kind": "crash_after_step"}],
        },
    ).json()["id"]
    assert client.get(f"/v1/runs/{run_id}").json()["interrupted"] is True


def test_errors_from_the_guard_still_carry_cors_headers():
    """Regression: a failing database gave a 500 without CORS headers, which the browser reports
    as a network failure, hiding the real error."""
    client, store = make_client(cors_origins="http://localhost:3000")

    def boom(*a, **k):
        raise RuntimeError("database down")

    store.list_runs = boom
    origin = {"Origin": "http://localhost:3000"}
    resp = client.get("/v1/runs", headers=origin)
    assert resp.status_code == 500 and resp.json()["error"]["code"] == "internal_error"
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"

    big = client.post(
        "/v1/runs", content=b"x" * 2_000_000, headers={**origin, "Content-Type": "application/json"}
    )
    assert big.status_code == 413
    assert big.headers["access-control-allow-origin"] == "http://localhost:3000"
