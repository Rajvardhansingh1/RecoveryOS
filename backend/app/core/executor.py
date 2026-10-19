"""Durable executor: drives a run one step at a time from database state.

Nothing is kept in memory between steps. After a crash, calling `run_until_blocked` again
rebuilds everything from committed rows: steps that have a committed result are skipped; a
step left `running` is redone, and for side-effecting steps the idempotency key (checked in the
database, then asked of the remote) decides whether the effect already happened.
"""

import time
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.adapters.github import GitHubAdapter
from app.adapters.llm import LLMClient
from app.core import steps as logic
from app.core.approval import check_gate
from app.core.errors import (
    ApprovalRequired,
    InvalidToolOutput,
    NothingToRelease,
    RecoveryError,
)
from app.core.faults import call_tool, maybe_crash
from app.core.idempotency import idempotency_key
from app.core.retry import RetryPolicy
from app.db.store import Store, utcnow
from app.models.domain import (
    STEP_ORDER,
    ApprovalOutput,
    ApprovalStatus,
    ChangelogOutput,
    CheckResult,
    ChecksOutput,
    FaultKind,
    InspectOutput,
    PlanOutput,
    Producer,
    PublishOutput,
    ReleasePlan,
    RepoSnapshot,
    RunStatus,
    StepName,
    StepStatus,
    VersionOutput,
)

M = TypeVar("M", bound=BaseModel)
TERMINAL = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.REJECTED}


class Executor:
    def __init__(
        self,
        store: Store,
        adapter: GitHubAdapter,
        llm_factory: Callable[[int], LLMClient | None],
        *,
        policy: RetryPolicy | None = None,
        approval_ttl_minutes: int = 60,
        llm_call_cap: int = 20,
        sleep: Callable[[float], None] = time.sleep,
        before_step: Callable[[str, StepName], None] | None = None,
    ):
        self.store = store
        self.adapter = adapter
        self._llm_factory = llm_factory
        self.policy = policy or RetryPolicy()
        self.ttl = approval_ttl_minutes
        self.llm_cap = llm_call_cap
        self._sleep = sleep
        # Observer called before each step starts. Used by the benchmark to model events that
        # happen between steps (an approval revoked, time passing). Not used in production.
        self._before_step = before_step

    # ------------------------------------------------------------------ public

    def run_until_blocked(self, run_id: str) -> RunStatus:
        """Advance until the run completes, fails, or waits for a human."""
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["status"] in TERMINAL:
            return RunStatus(run["status"])
        existing = self.store.get_steps(run_id)
        interrupted = [
            n
            for n, s in existing.items()
            if s["status"] == StepStatus.RUNNING and n != StepName.AWAIT_APPROVAL
        ]
        if interrupted:
            self.store.add_audit(
                run_id,
                "resumed_after_interruption",
                None,
                {
                    "interrupted_steps": interrupted,
                    "last_checkpoint": (self.store.checkpoints_for(run_id) or [{"seq": 0}])[-1][
                        "seq"
                    ],
                },
            )
        self.store.set_run_status(
            run_id, RunStatus.RUNNING, event="run_resumed" if existing else "run_started"
        )
        used = sum(s["llm_attempts"] for s in existing.values())
        client = self._llm_factory(max(0, self.llm_cap - used))

        for step in STEP_ORDER:
            done = self.store.get_steps(run_id).get(step)
            if done and done["status"] in (StepStatus.SUCCEEDED, StepStatus.SKIPPED):
                continue
            outcome = self._run_step(run_id, run["repo"], step, client)
            if outcome is not None:
                return outcome
        self.store.set_run_status(run_id, RunStatus.COMPLETED, event="run_completed")
        return RunStatus.COMPLETED

    def approve(self, run_id: str, by: str) -> RunStatus:
        if not self.store.decide_approval(run_id, ApprovalStatus.APPROVED, by, self.ttl):
            raise ApprovalRequired("no pending approval to approve")
        return self.run_until_blocked(run_id)

    def reject(self, run_id: str, by: str) -> RunStatus:
        if not self.store.decide_approval(run_id, ApprovalStatus.REJECTED, by):
            raise ApprovalRequired("no pending approval to reject")
        self.store.set_run_status(run_id, RunStatus.REJECTED, event="run_rejected")
        return RunStatus.REJECTED

    def revoke(self, run_id: str, by: str) -> bool:
        return self.store.decide_approval(run_id, ApprovalStatus.REVOKED, by)

    def finish_self_managed(self, run_id: str, by: str) -> RunStatus:
        """The owner will implement the plan themselves: end the flow without publishing."""
        if not self.store.decide_approval(run_id, ApprovalStatus.SELF_MANAGED, by):
            raise ApprovalRequired("no pending decision to record")
        return self.run_until_blocked(run_id)

    # Steps whose results depend on the plan and are redone when the owner suggests a change.
    _REVISED_STEPS = (
        StepName.INSPECT_REPO,
        StepName.RUN_CHECKS,
        StepName.PREPARE_CHANGELOG,
        StepName.VALIDATE_VERSION,
        StepName.CREATE_RELEASE_PLAN,
        StepName.AWAIT_APPROVAL,
        StepName.PUBLISH_RELEASE,
    )

    def prepare_revision(self, run_id: str, suggestion: str) -> int:
        """Record an owner suggestion and reset everything the plan depends on.

        The pending approval (if any) is revoked on record and every plan-dependent step is
        reset; the run is marked running so the caller can execute it in the background. The
        earlier plan stays in plan_revisions and the audit log. Returns the suggestion number.
        """
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        if run["status"] != RunStatus.AWAITING_APPROVAL:
            raise ApprovalRequired("the plan can only be revised while it waits for a decision")
        seq = self.store.add_suggestion(run_id, suggestion)
        self.store.decide_approval(run_id, ApprovalStatus.REVOKED, "system:revision")
        for step in self._REVISED_STEPS:
            if step in self.store.get_steps(run_id):
                self.store.reset_step(run_id, step, f"plan revision {seq + 1}")
        self.store.set_run_status(run_id, RunStatus.RUNNING, event="revision_started")
        return seq

    def revise(self, run_id: str, suggestion: str) -> RunStatus:
        """Add an owner suggestion, rebuild the plan and re-check everything."""
        self.prepare_revision(run_id, suggestion)
        return self.run_until_blocked(run_id)

    # ------------------------------------------------------------------ step loop

    def _run_step(
        self, run_id: str, repo: str, step: StepName, client: LLMClient | None
    ) -> RunStatus | None:
        """Returns a RunStatus if the run must stop here, or None to continue."""
        if step == StepName.AWAIT_APPROVAL:
            return self._await_approval(run_id)

        if self._before_step is not None:
            self._before_step(run_id, step)
        while True:
            attempt = self.store.begin_attempt(run_id, step)
            try:
                result, key, extra = self._do(run_id, repo, step, client)
                self._maybe_crash(run_id, step, FaultKind.CRASH_BEFORE_PERSIST)
                self.store.commit_step_success(
                    run_id,
                    step,
                    result.output.model_dump(mode="json"),
                    produced_by=result.produced_by,
                    provider=result.provider,
                    model=result.model,
                    fallback_reason=result.fallback_reason,
                    llm_attempts=result.llm_calls,
                    tokens=result.tokens,
                    idempotency_key=key,
                    extra=extra,
                )
                self._maybe_crash(run_id, step, FaultKind.CRASH_AFTER_STEP)
            except ApprovalRequired as exc:
                return self._approval_blocked(run_id, step, exc)
            except RecoveryError as exc:
                if exc.retryable and self.policy.allows(attempt):
                    self.store.mark_step_failure(
                        run_id, step, StepStatus.FAILED_RETRYABLE, exc.code, str(exc)
                    )
                    delay = self.policy.delay(attempt, getattr(exc, "retry_after", 0.0))
                    self.store.add_audit(
                        run_id,
                        "retry_scheduled",
                        step,
                        {"attempt": attempt, "delay_s": round(delay, 3)},
                    )
                    self._sleep(delay)
                    continue
                self.store.mark_step_failure(
                    run_id, step, StepStatus.FAILED_TERMINAL, exc.code, str(exc)
                )
                self.store.set_run_status(
                    run_id,
                    RunStatus.FAILED,
                    "retries_exhausted" if exc.retryable else exc.code,
                    event="run_failed",
                )
                return RunStatus.FAILED

            if step == StepName.RUN_CHECKS and result.output.verdict == "fail":  # type: ignore[attr-defined]
                self.store.set_run_status(
                    run_id, RunStatus.FAILED, "checks_failed", event="run_failed"
                )
                return RunStatus.FAILED
            return None

    # ------------------------------------------------------------------ approval

    def _plan(self, run_id: str) -> ReleasePlan:
        out = self._output(run_id, StepName.CREATE_RELEASE_PLAN, PlanOutput)
        return out.plan

    def _await_approval(self, run_id: str) -> RunStatus | None:
        plan = self._plan(run_id)
        current_hash = logic.compute_plan_hash(plan)
        self.store.ensure_plan_revision(
            run_id, plan.revision, current_hash, plan.model_dump(mode="json")
        )
        approval = self.store.latest_approval(run_id)
        usable = (
            approval is not None
            and approval["plan_hash"] == current_hash
            and (
                # Choosing to implement it yourself grants no authority, so it does not expire.
                approval["status"] == ApprovalStatus.SELF_MANAGED
                or (
                    approval["status"] in (ApprovalStatus.PENDING, ApprovalStatus.APPROVED)
                    and (approval["expires_at"] is None or approval["expires_at"] > utcnow())
                )
            )
        )
        if not usable:
            self.store.create_approval(run_id, current_hash, self.ttl)
            approval = self.store.latest_approval(run_id)
        if approval is None:
            raise RecoveryError("approval missing after creation")
        if approval["status"] == ApprovalStatus.PENDING:
            self.store.set_run_status(
                run_id, RunStatus.AWAITING_APPROVAL, event="awaiting_approval"
            )
            return RunStatus.AWAITING_APPROVAL
        if approval["status"] == ApprovalStatus.SELF_MANAGED:
            self.store.begin_attempt(run_id, StepName.AWAIT_APPROVAL)
            self.store.commit_step_success(
                run_id,
                StepName.AWAIT_APPROVAL,
                ApprovalOutput(
                    approval_id=approval["id"], plan_hash=approval["plan_hash"]
                ).model_dump(),
                produced_by=Producer.HUMAN,
                extra={"decision": "implement_myself"},
            )
            self.store.mark_step_skipped(
                run_id, StepName.PUBLISH_RELEASE, "the owner chose to implement the plan themselves"
            )
            self.store.set_run_status(run_id, RunStatus.COMPLETED, event="run_completed")
            return RunStatus.COMPLETED
        # Approved: record the human decision as this step's result.
        self.store.begin_attempt(run_id, StepName.AWAIT_APPROVAL)
        self.store.commit_step_success(
            run_id,
            StepName.AWAIT_APPROVAL,
            ApprovalOutput(
                approval_id=approval["id"], plan_hash=approval["plan_hash"]
            ).model_dump(),
            produced_by=Producer.HUMAN,
        )
        return None

    def _approval_blocked(self, run_id: str, step: StepName, exc: ApprovalRequired) -> RunStatus:
        """The gate refused at execution time: undo the stale approval step and ask again."""
        self.store.add_audit(run_id, "publish_blocked", step, {"reason": exc.reason})
        self.store.mark_step_failure(
            run_id, step, StepStatus.FAILED_RETRYABLE, exc.code, exc.reason
        )
        if exc.reason.startswith("repository changed"):
            # The plan itself is stale; a new approval cannot fix that.
            self.store.set_run_status(run_id, RunStatus.FAILED, "stale_plan", event="run_failed")
            return RunStatus.FAILED
        # The gate refused this approval, so it must not be reused: revoke it on record.
        self.store.decide_approval(run_id, ApprovalStatus.REVOKED, "system:gate")
        self.store.reset_step(run_id, StepName.AWAIT_APPROVAL, exc.reason)
        self.store.set_run_status(run_id, RunStatus.AWAITING_APPROVAL, event="awaiting_approval")
        # Re-enter the loop so a fresh pending approval is created for the human.
        self._await_approval(run_id)
        return RunStatus.AWAITING_APPROVAL

    # ------------------------------------------------------------------ doing a step

    def _output(self, run_id: str, step: StepName, model: type[M]) -> M:
        row = self.store.get_steps(run_id)[step]
        return model.model_validate(row["output"])

    def _do(
        self, run_id: str, repo: str, step: StepName, client: LLMClient | None
    ) -> tuple[logic.StepResult, str | None, dict[str, Any]]:
        if step == StepName.INSPECT_REPO:
            raw = self._tool(run_id, step, lambda: self.adapter.get_repo_snapshot(repo))
            snap = self._parse(RepoSnapshot, raw)
            if not snap.commits:
                raise NothingToRelease("no commits since the last release tag")
            return logic.inspect_repo(client, snap), None, {}

        snap = self._output(run_id, StepName.INSPECT_REPO, InspectOutput).snapshot

        if step == StepName.RUN_CHECKS:
            raw = self._tool(run_id, step, lambda: self.adapter.run_checks(repo, snap.head_sha))
            if not isinstance(raw, list):
                raise InvalidToolOutput("checks result is not a list")
            results = [self._parse(CheckResult, r) for r in raw]
            return logic.run_checks(client, results), None, {}

        if step == StepName.PREPARE_CHANGELOG:
            suggestions = self.store.suggestions_for(run_id)
            return logic.prepare_changelog(client, snap.commits, suggestions), None, {}

        if step == StepName.VALIDATE_VERSION:
            suggestions = self.store.suggestions_for(run_id)
            return logic.validate_version(client, snap, suggestions), None, {}

        checks = self._output(run_id, StepName.RUN_CHECKS, ChecksOutput)
        changelog = self._output(run_id, StepName.PREPARE_CHANGELOG, ChangelogOutput)
        version = self._output(run_id, StepName.VALIDATE_VERSION, VersionOutput)

        if step == StepName.CREATE_RELEASE_PLAN:
            suggestions = self.store.suggestions_for(run_id)
            res = logic.create_release_plan(client, snap, checks, version, changelog, suggestions)
            plan: ReleasePlan = res.output  # type: ignore[assignment]
            key = idempotency_key(run_id, step, {"plan_hash": plan.plan_hash})
            effect, reconciled = self._side_effect(
                run_id,
                step,
                key,
                lambda: self.adapter.create_draft_release(
                    run_id, key, plan.repo, plan.tag, plan.head_sha, plan.notes_markdown
                ),
                ("draft_ref",),
            )
            ref = effect.get("draft_ref")
            if not isinstance(ref, str):
                raise InvalidToolOutput("draft reference missing")
            res.output = PlanOutput(plan=plan, draft_ref=ref)
            return res, key, {"reconciled": reconciled}

        if step == StepName.PUBLISH_RELEASE:
            plan = self._plan(run_id)
            # Execution-time authorization: the last thing checked before the side effect.
            approval_id = check_gate(self.store, self.adapter, run_id, plan)
            key = idempotency_key(run_id, step, {"plan_hash": logic.compute_plan_hash(plan)})
            effect, reconciled = self._side_effect(
                run_id,
                step,
                key,
                lambda: self.adapter.publish_release(
                    run_id, key, plan.repo, plan.tag, plan.head_sha, plan.notes_markdown
                ),
                ("release_url", "tag"),
            )
            url, tag = effect.get("release_url"), effect.get("tag")
            if not isinstance(url, str) or not isinstance(tag, str):
                raise InvalidToolOutput("release result malformed")
            out = PublishOutput(release_url=url, tag=tag, reconciled=reconciled)
            return (
                logic.StepResult(out, Producer.SYSTEM),
                key,
                {"approval_id": approval_id, "reconciled": reconciled},
            )

        raise AssertionError(f"unhandled step {step}")

    # ------------------------------------------------------------------ tools and faults

    def _tool(self, run_id: str, step: StepName, fn: Callable[[], Any]) -> Any:
        return call_tool(self.store, run_id, step, fn)

    def _side_effect(
        self,
        run_id: str,
        step: StepName,
        key: str,
        call: Callable[[], dict[str, Any]],
        required: tuple[str, ...],
    ) -> tuple[dict[str, Any], bool]:
        """Run a side effect at most once per key. Returns (result, reconciled).

        Order of defences: our own record of the key, then the remote's record of the key,
        and only then the call itself. A reply is validated before it is recorded, so a
        corrupt reply can never be saved as if it were the effect's result.
        """
        cached = self.store.get_idempotent(key)
        if cached is not None:
            self.store.add_audit(run_id, "effect_reused", step, {"source": "local_key"})
            return cached, True
        found = self.adapter.find_effect(key)
        if found is not None:
            self.store.put_idempotent(key, run_id, step, found)
            self.store.add_audit(run_id, "effect_reused", step, {"source": "remote_key"})
            return found, True
        result = self._tool(run_id, step, call)
        if not isinstance(result, dict) or not all(
            isinstance(result.get(k), str) for k in required
        ):
            raise InvalidToolOutput("effect result malformed")
        # Crash window: the remote has acted but nothing local records it yet.
        self._maybe_crash(run_id, step, FaultKind.CRASH_BEFORE_PERSIST)
        self.store.put_idempotent(key, run_id, step, result)
        return result, False

    def _maybe_crash(self, run_id: str, step: StepName, kind: FaultKind) -> None:
        maybe_crash(self.store, run_id, step, kind)

    @staticmethod
    def _parse(model: type[M], raw: Any) -> M:
        try:
            return model.model_validate(raw)
        except ValidationError as exc:
            raise InvalidToolOutput(f"{model.__name__} failed validation") from exc
