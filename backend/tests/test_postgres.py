"""Integration tests against a real Postgres (the Supabase CLI local database).

Skipped unless RECOVERYOS_TEST_DATABASE_URL is set. The schema comes from the SQL migration,
not from SQLAlchemy, so these tests also check that the migration and the code agree.
Each test removes the rows it created.
"""

import os

import pytest
from helpers import ScriptedLLM, good_replies
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.adapters.github import _FIXTURES, MockGitHubAdapter
from app.core.errors import SimulatedCrash
from app.core.executor import Executor
from app.core.retry import RetryPolicy
from app.db.store import Store, make_engine
from app.models.domain import ApprovalStatus, Fault, FaultKind, RunStatus, StepName

URL = os.environ.get("RECOVERYOS_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="RECOVERYOS_TEST_DATABASE_URL not set")

REPO = "demo/acme-api"


def sa_url(url: str) -> str:
    return (
        "postgresql+psycopg://" + url.split("://", 1)[1] if url.startswith("postgresql://") else url
    )


@pytest.fixture
def pg():
    store = Store(make_engine(sa_url(URL)))
    created: list[str] = []
    yield store, created
    with store.tx() as conn:
        for rid in created:
            conn.execute(text("delete from remote_effects where run_id = :r"), {"r": rid})
            conn.execute(text("delete from runs where id = :r"), {"r": rid})
    store.engine.dispose()


def executor(store, llm=None):
    return Executor(
        store,
        MockGitHubAdapter(store),
        lambda cap: llm,
        policy=RetryPolicy(max_attempts=3, base_delay=0.0),
        sleep=lambda s: None,
    )


def new_run(pg, faults=()):
    store, created = pg
    rid = store.create_run(REPO, list(faults))["id"]
    created.append(rid)
    return rid


def test_full_flow_with_llm_labels_survives_jsonb_round_trip(pg):
    store, _ = pg
    llm = ScriptedLLM(good_replies(_FIXTURES[REPO]["commits"]))
    rid = new_run(pg)
    ex = executor(store, llm)
    assert ex.run_until_blocked(rid) == RunStatus.AWAITING_APPROVAL
    assert ex.approve(rid, "alice") == RunStatus.COMPLETED
    steps = store.get_steps(rid)
    assert steps["inspect_repo"]["produced_by"] == "llm"
    assert steps["inspect_repo"]["output"]["snapshot"]["head_sha"].startswith("a1b2c3d4")
    assert steps["publish_release"]["output"]["tag"] == "v1.5.0"
    applied = [
        e
        for e in store.remote_effects_for(rid)
        if e["kind"] == "applied" and e["action"] == "publish"
    ]
    assert len(applied) == 1


def test_crash_resume_in_a_new_process_and_engine(pg):
    store, _ = pg
    rid = new_run(
        pg, [Fault(step=StepName.CREATE_RELEASE_PLAN, kind=FaultKind.CRASH_BEFORE_PERSIST)]
    )
    with pytest.raises(SimulatedCrash):
        executor(store).run_until_blocked(rid)

    # A "restart": brand-new engine, store and executor on the same database.
    fresh = Store(make_engine(sa_url(URL)))
    try:
        assert executor(fresh).run_until_blocked(rid) == RunStatus.AWAITING_APPROVAL
        drafts = [
            e
            for e in fresh.remote_effects_for(rid)
            if e["kind"] == "applied" and e["action"] == "create_draft"
        ]
        assert len(drafts) == 1
        assert any(e["event"] == "resumed_after_interruption" for e in fresh.audit_events(rid))
    finally:
        fresh.engine.dispose()


def test_duplicate_idempotency_key_is_rejected_by_the_database(pg):
    store, _ = pg
    rid = new_run(pg)
    assert store.put_idempotent("pg-key-1", rid, StepName.PUBLISH_RELEASE, {"a": 1}) is True
    assert store.put_idempotent("pg-key-1", rid, StepName.PUBLISH_RELEASE, {"a": 2}) is False
    assert store.get_idempotent("pg-key-1") == {"a": 1}


def test_expired_and_revoked_approvals_block_publish_on_postgres(pg):
    store, _ = pg
    rid = new_run(pg)
    ex = executor(store)
    ex.run_until_blocked(rid)
    store.decide_approval(rid, ApprovalStatus.APPROVED, "alice", ttl_minutes=60)
    assert ex.revoke(rid, "alice")
    assert ex.run_until_blocked(rid) == RunStatus.AWAITING_APPROVAL
    assert not [e for e in store.remote_effects_for(rid) if e["action"] == "publish"]


def test_audit_log_is_append_only(pg):
    store, _ = pg
    rid = new_run(pg)
    with pytest.raises(DBAPIError, match="append-only"):
        with store.tx() as conn:
            conn.execute(
                text("update audit_events set event = 'tampered' where run_id = :r"), {"r": rid}
            )


def test_audit_sequence_is_unique_per_run(pg):
    store, _ = pg
    rid = new_run(pg)
    with pytest.raises(DBAPIError):
        with store.tx() as conn:
            conn.execute(
                text(
                    "insert into audit_events (run_id, seq, ts, event, detail) "
                    "values (:r, 1, now(), 'dup', '{}'::jsonb)"
                ),
                {"r": rid},
            )


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_supabase_api_roles_cannot_read_or_write_any_table(pg, role):
    store, _ = pg
    tables = [
        "runs",
        "steps",
        "checkpoints",
        "approvals",
        "audit_events",
        "idempotency_keys",
        "remote_effects",
        "suggestions",
        "plan_revisions",
    ]
    for table in tables:
        with pytest.raises(DBAPIError, match="permission denied"):
            with store.tx() as conn:
                conn.execute(text(f"set local role {role}"))
                conn.execute(text(f"select count(*) from public.{table}"))
    with pytest.raises(DBAPIError, match="permission denied"):
        with store.tx() as conn:
            conn.execute(text(f"set local role {role}"))
            conn.execute(
                text(
                    "insert into public.runs (id, status, repo, faults, created_at, updated_at) "
                    "values ('x', 'created', 'a/b', '[]'::jsonb, now(), now())"
                )
            )


def test_row_level_security_is_enabled_on_every_public_table(pg):
    store, _ = pg
    with store.tx() as conn:
        rows = conn.execute(
            text("select tablename from pg_tables where schemaname = 'public' and not rowsecurity")
        ).all()
    assert rows == []


def test_data_persists_across_a_new_connection_pool(pg):
    store, _ = pg
    rid = new_run(pg)
    other = Store(make_engine(sa_url(URL)))
    try:
        assert other.get_run(rid)["repo"] == REPO
    finally:
        other.engine.dispose()


def test_revision_flow_persists_suggestions_and_plan_revisions(pg):
    store, _ = pg
    rid = new_run(pg)
    ex = executor(store)
    ex.run_until_blocked(rid)
    ex.revise(rid, "Call out the pagination change.")
    fresh = Store(make_engine(sa_url(URL)))
    try:
        assert fresh.suggestions_for(rid) == ["Call out the pagination change."]
        assert [r["revision"] for r in fresh.plan_revisions_for(rid)] == [1, 2]
        assert ex.finish_self_managed(rid, "alice") == RunStatus.COMPLETED
        assert fresh.get_steps(rid)["publish_release"]["status"] == "skipped"
        assert not [e for e in fresh.remote_effects_for(rid) if e["action"] == "publish"]
    finally:
        fresh.engine.dispose()
