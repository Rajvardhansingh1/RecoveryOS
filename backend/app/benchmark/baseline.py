"""The naive baseline the benchmark compares against.

It uses the same adapter, the same step logic and the same LLM client as RecoveryOS, and faces
the same injected faults. What it lacks is exactly what RecoveryOS adds:

  * state lives in memory only, so a crash restarts the whole workflow from step one;
  * side effects carry no idempotency key and replies to them are accepted without validation;
  * retries are bounded the same way, but nothing makes them safe;
  * approval is read once when the workflow reaches the approval step and never re-checked
    when the release is published.
"""

from typing import Any

from pydantic import ValidationError

from app.adapters.github import GitHubAdapter
from app.adapters.llm import LLMClient
from app.core import steps as logic
from app.core.errors import InvalidToolOutput, RecoveryError
from app.core.faults import call_tool, maybe_crash
from app.db.store import Store
from app.models.domain import (
    ApprovalStatus,
    CheckResult,
    FaultKind,
    PlanOutput,
    Producer,
    PublishOutput,
    RepoSnapshot,
    StepName,
)


class NaiveRunner:
    def __init__(
        self,
        store: Store,
        adapter: GitHubAdapter,
        llm: LLMClient | None,
        max_attempts: int,
        before_step=None,
    ):
        self.store, self.adapter, self.llm = store, adapter, llm
        self.max_attempts = max_attempts
        self._before_step = before_step
        self.done: dict[str, dict[str, Any]] = {}  # in-memory only: lost with the process
        self.labels: dict[str, str] = {}
        self.info: dict[str, dict[str, Any]] = {}
        self.approved = False

    # ---- harness interface ----

    def start(self, run_id: str) -> str:
        return self._run(run_id)

    def finish(self, run_id: str) -> str:
        return self._run(run_id)

    def reject(self, run_id: str) -> str:
        self.store.decide_approval(run_id, ApprovalStatus.REJECTED, "benchmark")
        return "rejected"

    def completed_outputs(self, run_id: str) -> dict[str, dict[str, Any]]:
        return dict(self.done)

    def step_labels(self, run_id: str) -> dict[str, str]:
        return dict(self.labels)

    def step_info(self, run_id: str) -> dict[str, dict[str, Any]]:
        return dict(self.info)

    # ---- internals ----

    def _attempt(self, fn):
        for attempt in range(1, self.max_attempts + 1):
            try:
                return fn()
            except RecoveryError as exc:
                if not exc.retryable or attempt >= self.max_attempts:
                    raise
        raise AssertionError("unreachable")

    @staticmethod
    def _parse(model, raw):
        try:
            return model.model_validate(raw)
        except ValidationError as exc:
            raise InvalidToolOutput(model.__name__) from exc

    def _record(self, run_id: str, step: StepName, result: logic.StepResult, output) -> None:
        maybe_crash(self.store, run_id, step, FaultKind.CRASH_BEFORE_PERSIST)
        self.done[step] = output.model_dump(mode="json")
        self.labels[step] = result.produced_by
        self.info[step] = {
            "produced_by": result.produced_by,
            "model": result.model,
            "fallback_reason": result.fallback_reason,
        }
        maybe_crash(self.store, run_id, step, FaultKind.CRASH_AFTER_STEP)

    def _run(self, run_id: str) -> str:
        repo = self.store.get_run(run_id)["repo"]
        try:
            return self._pipeline(run_id, repo)
        except RecoveryError as exc:
            return f"failed:{'retries_exhausted' if exc.retryable else exc.code}"

    def _pipeline(self, run_id: str, repo: str) -> str:
        S = StepName
        tool = lambda step, fn: call_tool(self.store, run_id, step, fn)  # noqa: E731

        if S.INSPECT_REPO not in self.done:

            def work():
                snap = self._parse(
                    RepoSnapshot, tool(S.INSPECT_REPO, lambda: self.adapter.get_repo_snapshot(repo))
                )
                return logic.inspect_repo(self.llm, snap)

            res = self._attempt(work)
            self._record(run_id, S.INSPECT_REPO, res, res.output)
        snap = RepoSnapshot.model_validate(self.done[S.INSPECT_REPO]["snapshot"])

        if S.RUN_CHECKS not in self.done:

            def work():
                raw = tool(S.RUN_CHECKS, lambda: self.adapter.run_checks(repo, snap.head_sha))
                if not isinstance(raw, list):
                    raise InvalidToolOutput("checks")
                return logic.run_checks(self.llm, [self._parse(CheckResult, r) for r in raw])

            res = self._attempt(work)
            self._record(run_id, S.RUN_CHECKS, res, res.output)
        if self.done[S.RUN_CHECKS]["verdict"] == "fail":
            return "failed:checks_failed"

        if S.PREPARE_CHANGELOG not in self.done:
            res = self._attempt(lambda: logic.prepare_changelog(self.llm, snap.commits))
            self._record(run_id, S.PREPARE_CHANGELOG, res, res.output)
        if S.VALIDATE_VERSION not in self.done:
            res = self._attempt(lambda: logic.validate_version(self.llm, snap))
            self._record(run_id, S.VALIDATE_VERSION, res, res.output)

        from app.models.domain import ChangelogOutput, ChecksOutput, VersionOutput

        if S.CREATE_RELEASE_PLAN not in self.done:
            checks = ChecksOutput.model_validate(self.done[S.RUN_CHECKS])
            changelog = ChangelogOutput.model_validate(self.done[S.PREPARE_CHANGELOG])
            version = VersionOutput.model_validate(self.done[S.VALIDATE_VERSION])

            def work():
                res = logic.create_release_plan(self.llm, snap, checks, version, changelog)
                plan = res.output
                reply = tool(
                    S.CREATE_RELEASE_PLAN,
                    lambda: self.adapter.create_draft_release(
                        run_id, None, plan.repo, plan.tag, plan.head_sha, plan.notes_markdown
                    ),
                )
                maybe_crash(
                    self.store, run_id, S.CREATE_RELEASE_PLAN, FaultKind.CRASH_BEFORE_PERSIST
                )
                ref = reply.get("draft_ref") if isinstance(reply, dict) else None
                res.output = PlanOutput(plan=plan, draft_ref=str(ref))  # accepted unvalidated
                return res

            res = self._attempt(work)
            self._record(run_id, S.CREATE_RELEASE_PLAN, res, res.output)
        plan_hash = self.done[S.CREATE_RELEASE_PLAN]["plan"]["plan_hash"]

        # Approval is read once, here.
        approval = self.store.latest_approval(run_id)
        if approval is None or approval["plan_hash"] != plan_hash:
            self.store.create_approval(run_id, plan_hash, 60)
            approval = self.store.latest_approval(run_id)
        if approval["status"] == ApprovalStatus.REJECTED:
            return "rejected"
        if approval["status"] != ApprovalStatus.APPROVED:
            return "awaiting_approval"
        self.approved = True

        if S.PUBLISH_RELEASE not in self.done:
            if self._before_step is not None:
                self._before_step(run_id, S.PUBLISH_RELEASE)
            plan = PlanOutput.model_validate(self.done[S.CREATE_RELEASE_PLAN]).plan

            def work():
                reply = tool(
                    S.PUBLISH_RELEASE,
                    lambda: self.adapter.publish_release(
                        run_id, None, plan.repo, plan.tag, plan.head_sha, plan.notes_markdown
                    ),
                )
                maybe_crash(self.store, run_id, S.PUBLISH_RELEASE, FaultKind.CRASH_BEFORE_PERSIST)
                reply = reply if isinstance(reply, dict) else {}
                out = PublishOutput(
                    release_url=str(reply.get("release_url")), tag=str(reply.get("tag"))
                )
                return logic.StepResult(out, Producer.SYSTEM)

            res = self._attempt(work)
            self._record(run_id, S.PUBLISH_RELEASE, res, res.output)
        return "completed"
