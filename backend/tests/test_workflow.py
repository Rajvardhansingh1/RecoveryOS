"""Happy path, labeling, and the approval gate."""

from datetime import timedelta

import pytest
from sqlalchemy import update

from app.core.errors import ApprovalRequired
from app.db import tables as t
from app.db.store import utcnow
from app.models.domain import ApprovalStatus, Producer, RunStatus

REPO = "demo/acme-api"


def new_run(store, faults=()):
    return store.create_run(REPO, list(faults))["id"]


def effects(store, run_id, kind="applied", action=None):
    return [
        e
        for e in store.remote_effects_for(run_id)
        if e["kind"] == kind and (action is None or e["action"] == action)
    ]


def test_full_run_with_llm_pauses_then_publishes_after_approval(store, executor_factory, good_llm):
    ex = executor_factory(good_llm)
    run_id = new_run(store)

    assert ex.run_until_blocked(run_id) == RunStatus.AWAITING_APPROVAL
    assert effects(store, run_id, action="publish") == []  # nothing published before approval

    assert ex.approve(run_id, "alice") == RunStatus.COMPLETED
    steps = store.get_steps(run_id)
    assert all(s["status"] == "succeeded" for s in steps.values())
    assert len(effects(store, run_id, action="publish")) == 1
    assert steps["publish_release"]["output"]["tag"] == "v1.5.0"  # feat -> minor bump


def test_llm_steps_are_labeled_llm(store, executor_factory, good_llm):
    ex = executor_factory(good_llm)
    run_id = new_run(store)
    ex.run_until_blocked(run_id)
    steps = store.get_steps(run_id)
    for name in (
        "inspect_repo",
        "run_checks",
        "prepare_changelog",
        "validate_version",
        "create_release_plan",
    ):
        assert steps[name]["produced_by"] == Producer.LLM
        assert steps[name]["provider"] == "scripted-test"
        assert steps[name]["model"] == "scripted-1"
        assert steps[name]["fallback_reason"] is None


def test_without_llm_everything_is_labeled_fallback_with_reason(store, executor_factory):
    ex = executor_factory(None)
    run_id = new_run(store)
    assert ex.run_until_blocked(run_id) == RunStatus.AWAITING_APPROVAL
    steps = store.get_steps(run_id)
    for name in (
        "inspect_repo",
        "run_checks",
        "prepare_changelog",
        "validate_version",
        "create_release_plan",
    ):
        assert steps[name]["produced_by"] == Producer.FALLBACK
        assert steps[name]["fallback_reason"] == "llm_not_configured"


def test_human_approval_step_is_labeled_human(store, executor_factory):
    ex = executor_factory(None)
    run_id = new_run(store)
    ex.run_until_blocked(run_id)
    ex.approve(run_id, "alice")
    assert store.get_steps(run_id)["await_approval"]["produced_by"] == Producer.HUMAN


def test_failing_checks_end_the_run_before_any_write(store, executor_factory):
    ex = executor_factory(None)
    run_id = store.create_run("demo/failing-checks", [])["id"]
    assert ex.run_until_blocked(run_id) == RunStatus.FAILED
    assert store.get_run(run_id)["error_code"] == "checks_failed"
    assert store.remote_effects_for(run_id) == []


# ---- approval gate ----


def test_cannot_approve_without_pending_approval(store, executor_factory):
    ex = executor_factory(None)
    run_id = new_run(store)
    with pytest.raises(ApprovalRequired):
        ex.approve(run_id, "alice")


def test_rejection_ends_run_and_nothing_is_published(store, executor_factory):
    ex = executor_factory(None)
    run_id = new_run(store)
    ex.run_until_blocked(run_id)
    assert ex.reject(run_id, "alice") == RunStatus.REJECTED
    assert ex.run_until_blocked(run_id) == RunStatus.REJECTED
    assert effects(store, run_id, action="publish") == []


def test_expired_approval_is_rechecked_at_execution_time_and_blocks_publish(
    store, executor_factory, monkeypatch
):
    import app.core.approval as approval_module

    ex = executor_factory(None)
    run_id = new_run(store)
    ex.run_until_blocked(run_id)
    # The approval is valid when the approval step records it, but time passes before the
    # publish step runs: only the gate's own check at execution time can catch that.
    real_now = utcnow
    monkeypatch.setattr(approval_module, "utcnow", lambda: real_now() + timedelta(hours=2))
    assert ex.approve(run_id, "alice") == RunStatus.AWAITING_APPROVAL
    assert store.get_steps(run_id).get("await_approval") is None  # committed, then reset
    assert store.get_steps(run_id)["publish_release"]["status"] == "failed_retryable"
    assert effects(store, run_id, action="publish") == []
    events = [e["event"] for e in store.audit_events(run_id)]
    assert "publish_blocked" in events
    # A fresh pending approval was requested, and approving it completes the run.
    monkeypatch.undo()
    assert store.latest_approval(run_id)["status"] == ApprovalStatus.PENDING
    assert ex.approve(run_id, "alice") == RunStatus.COMPLETED
    assert len(effects(store, run_id, action="publish")) == 1


def test_revoked_approval_blocks_publish(store, executor_factory):
    ex = executor_factory(None)
    run_id = new_run(store)
    ex.run_until_blocked(run_id)
    store.decide_approval(run_id, ApprovalStatus.APPROVED, "alice", ttl_minutes=60)
    assert ex.revoke(run_id, "alice")
    assert ex.run_until_blocked(run_id) == RunStatus.AWAITING_APPROVAL
    assert effects(store, run_id, action="publish") == []


def test_approval_for_a_different_plan_is_refused(store, executor_factory):
    ex = executor_factory(None)
    run_id = new_run(store)
    ex.run_until_blocked(run_id)
    with store.tx() as conn:
        conn.execute(
            update(t.approvals).where(t.approvals.c.run_id == run_id).values(plan_hash="0" * 64)
        )
    store.decide_approval(run_id, ApprovalStatus.APPROVED, "alice", ttl_minutes=60)
    assert ex.run_until_blocked(run_id) == RunStatus.AWAITING_APPROVAL
    assert effects(store, run_id, action="publish") == []


def test_repository_change_after_approval_fails_run_without_publishing(
    store, executor_factory, adapter, monkeypatch
):
    ex = executor_factory(None)
    run_id = new_run(store)
    ex.run_until_blocked(run_id)
    store.decide_approval(run_id, ApprovalStatus.APPROVED, "alice", ttl_minutes=60)
    monkeypatch.setattr(adapter, "get_head_sha", lambda repo: "f" * 40)
    assert ex.run_until_blocked(run_id) == RunStatus.FAILED
    assert store.get_run(run_id)["error_code"] == "stale_plan"
    assert effects(store, run_id, action="publish") == []
