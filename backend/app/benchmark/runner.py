"""Runs scenarios against RecoveryOS and the naive baseline and measures what happened.

All measurements come from the database and the simulated remote ledger after the fact, not
from what a system reports about itself.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import update

from app.adapters.github import MockGitHubAdapter
from app.adapters.llm import LLMClient, LLMMeta
from app.benchmark.baseline import NaiveRunner
from app.benchmark.dataset import MAX_ATTEMPTS, Scenario
from app.core.errors import SimulatedCrash
from app.core.executor import Executor
from app.core.retry import RetryPolicy
from app.db import tables as t
from app.db.store import Store, make_engine, utcnow
from app.models.domain import (
    ApprovalOutput,
    ApprovalStatus,
    ChangelogOutput,
    ChecksOutput,
    Fault,
    InspectOutput,
    PlanOutput,
    PublishOutput,
    StepName,
    VersionOutput,
)

MAX_RESTARTS = 8
LLM_STEPS = [
    "inspect_repo",
    "run_checks",
    "prepare_changelog",
    "validate_version",
    "create_release_plan",
]
_OUTPUT_MODELS = {
    "inspect_repo": InspectOutput,
    "run_checks": ChecksOutput,
    "prepare_changelog": ChangelogOutput,
    "validate_version": VersionOutput,
    "create_release_plan": PlanOutput,
    "await_approval": ApprovalOutput,
    "publish_release": PublishOutput,
}


class CountingLLM:
    """Wraps any client to count calls and tokens, identically for both systems."""

    def __init__(self, inner: LLMClient):
        self._inner = inner
        self.provider = inner.provider
        self.tokens = 0
        self.calls = 0

    @property
    def calls_made(self) -> int:
        return self.calls

    def complete(self, system, user, schema):
        self.calls += 1
        try:
            obj, meta = self._inner.complete(system, user, schema)
        except Exception:
            raise
        self.tokens += meta.tokens
        return obj, LLMMeta(meta.provider, meta.model, calls=self.calls, tokens=meta.tokens)


@dataclass
class RunResult:
    scenario_id: str
    split: str
    system: str
    expected: str
    status: str
    recoverable: bool
    recovered_exactly_once: bool
    outcome_matches: bool
    publish_applied: int
    draft_applied: int
    duplicate_effects: int
    incorrectly_complete: int
    approval_bypass: bool
    restarts: int
    resume_ms: list[float] = field(default_factory=list)
    llm_calls: int = 0
    tokens: int = 0
    llm_steps: int = 0
    fallback_steps: int = 0
    stuck: bool = False
    models: dict[str, int] = field(default_factory=dict)
    fallback_reasons: dict[str, int] = field(default_factory=dict)


def output_valid(step: str, output: dict[str, Any]) -> bool:
    model = _OUTPUT_MODELS.get(step)
    if model is None:
        return True
    try:
        obj = model.model_validate(output)
    except ValidationError:
        return False
    if step == "create_release_plan":
        return str(obj.draft_ref).startswith("mock://")  # type: ignore[attr-defined]
    if step == "publish_release":
        return obj.release_url.startswith("mock://") and obj.tag.startswith("v")  # type: ignore[attr-defined]
    return True


class RecoverySystem:
    name = "recoveryos"

    def __init__(self, store, adapter, llm, before_step):
        self.store = store
        self.ex = Executor(
            store,
            adapter,
            lambda cap: llm,
            policy=RetryPolicy(max_attempts=MAX_ATTEMPTS, base_delay=0.0),
            sleep=lambda s: None,
            before_step=before_step,
        )

    def start(self, run_id):
        return self._status(self.ex.run_until_blocked(run_id), run_id)

    finish = start

    def reject(self, run_id):
        return self._status(self.ex.reject(run_id, "benchmark"), run_id)

    def _status(self, status, run_id):
        if status == "failed":
            code = self.store.get_run(run_id)["error_code"]
            return f"failed:{code}"
        return str(status)

    def completed_outputs(self, run_id):
        return {
            n: s["output"]
            for n, s in self.store.get_steps(run_id).items()
            if s["status"] == "succeeded"
        }

    def step_labels(self, run_id):
        return {
            n: s["produced_by"]
            for n, s in self.store.get_steps(run_id).items()
            if s["status"] == "succeeded"
        }

    def step_info(self, run_id):
        return {
            n: {
                "produced_by": s["produced_by"],
                "model": s["model"],
                "fallback_reason": s["fallback_reason"],
            }
            for n, s in self.store.get_steps(run_id).items()
            if s["status"] == "succeeded"
        }


def _applied(store: Store, run_id: str, action: str) -> int:
    return sum(
        1
        for e in store.remote_effects_for(run_id)
        if e["action"] == action and e["kind"] == "applied"
    )


def run_scenario(
    system: str, sc: Scenario, llm_factory: Callable[[], LLMClient | None]
) -> RunResult:
    store = Store(make_engine("sqlite+pysqlite:///:memory:"))
    store.create_schema()
    adapter = MockGitHubAdapter(store)
    run_id = store.create_run(sc.repo, [Fault(**f) for f in sc.faults])["id"]
    llm_inner = llm_factory()
    llm = CountingLLM(llm_inner) if llm_inner else None

    fired = {"done": False}

    def before_step(rid: str, step: StepName) -> None:
        """Events that happen between approval and publish."""
        if step != StepName.PUBLISH_RELEASE or fired["done"]:
            return
        fired["done"] = True
        if sc.approval == "revoke_before_publish":
            store.decide_approval(rid, ApprovalStatus.REVOKED, "benchmark")
        elif sc.approval == "expire_before_publish":
            with store.tx() as conn:
                conn.execute(
                    update(t.approvals)
                    .where(t.approvals.c.run_id == rid)
                    .values(expires_at=utcnow() - timedelta(minutes=1))
                )
        elif sc.approval == "head_change_before_publish":
            adapter.head_override = "f" * 40

    def factory():
        if system == "recoveryos":
            return RecoverySystem(store, adapter, llm, before_step)
        return NaiveRunner(store, adapter, llm, MAX_ATTEMPTS, before_step)

    current: list[Any] = [None]
    restarts, resume_ms, stuck = [0], [], [False]

    def drive(call: Callable[[Any], str]) -> str:
        crashed_before = restarts[0] > 0 and current[0] is None
        while True:
            if current[0] is None:
                current[0] = factory()
            started = time.perf_counter()
            try:
                status = call(current[0])
                if crashed_before:
                    resume_ms.append((time.perf_counter() - started) * 1000)
                return status
            except SimulatedCrash:
                restarts[0] += 1
                current[0] = None  # the process died; nothing in memory survives
                crashed_before = True
                if restarts[0] > MAX_RESTARTS:
                    stuck[0] = True
                    return "stuck"

    status = drive(lambda s: s.start(run_id))
    if status == "awaiting_approval" and sc.approval != "never":
        if sc.approval == "reject":
            status = drive(lambda s: s.reject(run_id))
        else:
            store.decide_approval(run_id, ApprovalStatus.APPROVED, "benchmark", ttl_minutes=60)
            status = drive(lambda s: s.finish(run_id))

    sys_obj = current[0] or factory()
    outputs = sys_obj.completed_outputs(run_id)
    labels = sys_obj.step_labels(run_id)
    info = sys_obj.step_info(run_id)
    models: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for name in LLM_STEPS:
        item = info.get(name)
        if not item:
            continue
        if item["produced_by"] == "llm" and item["model"]:
            models[item["model"]] = models.get(item["model"], 0) + 1
        elif item["produced_by"] == "deterministic_fallback":
            key = (item["fallback_reason"] or "unknown").split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
    drafts, pubs = _applied(store, run_id, "create_draft"), _applied(store, run_id, "publish")
    bad = 0
    for step, out in outputs.items():
        if not output_valid(step, out):
            bad += 1
        elif (step == "create_release_plan" and drafts == 0) or (
            step == "publish_release" and pubs == 0
        ):
            bad += 1  # marked complete but the remote never saw the effect

    if sc.expected == "no_publish":
        matches = pubs == 0
    else:
        matches = status == sc.expected
    result = RunResult(
        scenario_id=sc.id,
        split=sc.split,
        system=system,
        expected=sc.expected,
        status=status,
        recoverable=sc.recoverable,
        recovered_exactly_once=sc.recoverable
        and status == "completed"
        and pubs == 1
        and drafts == 1,
        outcome_matches=matches,
        publish_applied=pubs,
        draft_applied=drafts,
        duplicate_effects=max(0, drafts - 1) + max(0, pubs - 1),
        incorrectly_complete=bad,
        approval_bypass=sc.expected == "no_publish" and pubs > 0,
        restarts=restarts[0],
        resume_ms=resume_ms,
        llm_calls=llm.calls if llm else 0,
        tokens=llm.tokens if llm else 0,
        llm_steps=sum(1 for n in LLM_STEPS if labels.get(n) == "llm"),
        fallback_steps=sum(1 for n in LLM_STEPS if labels.get(n) == "deterministic_fallback"),
        stuck=stuck[0],
        models=models,
        fallback_reasons=reasons,
    )
    store.engine.dispose()
    return result
