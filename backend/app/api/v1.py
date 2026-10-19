"""HTTP routes. Thin: validation and mapping only; the logic lives in app.core."""

import threading

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.adapters.github import SUPPORTED_REPOS, is_demo_repo
from app.adapters.github_live import split_repo
from app.core.errors import (
    ApprovalRequired,
    InvalidToolOutput,
    NothingToRelease,
    SimulatedCrash,
    ToolRateLimited,
    ToolTimeout,
    UnsupportedRepo,
)
from app.core.executor import Executor
from app.core.plan_export import plan_filename, render_plan
from app.models.api import (
    ApprovalView,
    CreateRunRequest,
    DecisionRequest,
    RepoPreview,
    RunDetail,
    RunSummary,
    StepView,
    SuggestionRequest,
    TimelineEvent,
)
from app.models.domain import (
    STEP_ORDER,
    PlanOutput,
    Producer,
    ReleasePlan,
    RunStatus,
    StepName,
    StepStatus,
)

router = APIRouter(prefix="/v1")


class ActiveRuns:
    """Prevents the same run being executed twice at once inside this process."""

    def __init__(self):
        self._active: set[str] = set()
        self._lock = threading.Lock()

    def acquire(self, run_id: str) -> bool:
        with self._lock:
            if run_id in self._active:
                return False
            self._active.add(run_id)
            return True

    def release(self, run_id: str) -> None:
        with self._lock:
            self._active.discard(run_id)

    def is_active(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._active


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _ctx(request: Request):
    return request.app.state.ctx


def _execute(ctx, run_id: str) -> None:
    """Background job: advance a run. A simulated crash simply stops it, like a dead process."""
    executor: Executor = ctx.executor
    try:
        executor.run_until_blocked(run_id)
    except SimulatedCrash:
        ctx.store.add_audit(run_id, "process_crashed", None, {"simulated": True})
    finally:
        ctx.active.release(run_id)


def _summary(row) -> RunSummary:
    return RunSummary(
        id=row["id"],
        repo=row["repo"],
        source="demo" if is_demo_repo(row["repo"]) else "github",
        writes="simulated",
        status=row["status"],
        error_code=row["error_code"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _detail(ctx, row, was_active: bool = False) -> RunDetail:
    run_id = row["id"]
    steps = ctx.store.get_steps(run_id)
    views = [
        StepView(
            name=s["name"],
            status=s["status"],
            attempts=s["attempts"],
            produced_by=s["produced_by"],
            provider=s["provider"],
            model=s["model"],
            fallback_reason=s["fallback_reason"],
            llm_calls=s["llm_attempts"],
            tokens=s["tokens"],
            error_code=s["error_code"],
            output=s["output"],
            started_at=s["started_at"],
            finished_at=s["finished_at"],
        )
        for name in STEP_ORDER
        if (s := steps.get(name))
    ]
    approval = ctx.store.latest_approval(run_id)
    plan = _plan_of(steps)
    publish = steps.get(StepName.PUBLISH_RELEASE)
    outcome = None
    if row["status"] == RunStatus.COMPLETED and publish:
        outcome = "published" if publish["status"] == StepStatus.SUCCEEDED else "plan_delivered"
    elif row["status"] == RunStatus.REJECTED:
        outcome = "rejected"
    return RunDetail(
        **_summary(row).model_dump(),
        steps=views,
        approval=ApprovalView(
            id=approval["id"],
            status=approval["status"],
            plan_hash=approval["plan_hash"],
            expires_at=approval["expires_at"],
            decided_by=approval["decided_by"],
        )
        if approval
        else None,
        checkpoints=[
            {"seq": c["seq"], "step": c["step_name"], "state_hash": c["state_hash"]}
            for c in ctx.store.checkpoints_for(run_id)
        ],
        llm_steps=sum(1 for v in views if v.produced_by == Producer.LLM),
        fallback_steps=sum(1 for v in views if v.produced_by == Producer.FALLBACK),
        # "Interrupted" means: recorded as running, yet no worker held the run before the status
        # was read or after. Sampling on both sides keeps a run that finishes mid-request from
        # being reported as crashed.
        interrupted=row["status"] == RunStatus.RUNNING
        and not was_active
        and not ctx.active.is_active(run_id),
        remote_effects=[
            {
                "action": e["action"],
                "kind": e["kind"],
                "key": e["key"][:8],
                "created_at": e["created_at"].isoformat(),
            }
            for e in ctx.store.remote_effects_for(run_id)
        ],
        outcome=outcome,
        revision=plan.revision if plan else None,
        suggestions=ctx.store.suggestions_for(run_id),
        plan_revisions=[
            {
                "revision": r["revision"],
                "plan_hash": r["plan_hash"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in ctx.store.plan_revisions_for(run_id)
        ],
    )


def _plan_of(steps) -> ReleasePlan | None:
    step = steps.get(StepName.CREATE_RELEASE_PLAN)
    if not step or step["status"] != StepStatus.SUCCEEDED or not step["output"]:
        return None
    return PlanOutput.model_validate(step["output"]).plan


def _load(ctx, run_id: str):
    row = ctx.store.get_run(run_id)
    if row is None:
        raise _err(404, "run_not_found", "No such run.")
    return row


@router.get("/repos")
def list_repos(request: Request):
    return {
        "repos": list(SUPPORTED_REPOS),
        "github_link": _ctx(request).settings.enable_github_link,
    }


def _check_repo_allowed(ctx, repo: str) -> None:
    if is_demo_repo(repo):
        return
    if not ctx.settings.enable_github_link:
        raise _err(403, "github_link_disabled", "Linking a GitHub repository is turned off.")
    try:
        split_repo(repo)
    except UnsupportedRepo as exc:
        raise _err(
            422, "unsupported_repo", "Use the form owner/name for a public repository."
        ) from exc


@router.get("/repos/{owner}/{name}/preview", response_model=RepoPreview)
def preview_repo(owner: str, name: str, request: Request):
    """What the agent would read: demo fixtures or a public GitHub repository (read-only)."""
    ctx = _ctx(request)
    repo = f"{owner}/{name}"
    _check_repo_allowed(ctx, repo)
    try:
        data = ctx.executor.adapter.preview(repo)
    except UnsupportedRepo as exc:
        raise _err(404, "repo_not_found", str(exc)) from exc
    except NothingToRelease as exc:
        raise _err(
            409,
            "repo_empty",
            "This repository has no commits yet, so there is nothing to release. "
            "Push your code first.",
        ) from exc
    except ToolRateLimited as exc:
        raise _err(
            429,
            "github_rate_limited",
            "GitHub's rate limit was reached. Wait a minute, or set GITHUB_TOKEN on the server.",
        ) from exc
    except ToolTimeout as exc:
        raise _err(504, "github_timeout", "GitHub did not answer in time.") from exc
    except InvalidToolOutput as exc:
        raise _err(
            502, "github_bad_reply", f"GitHub returned something unexpected ({exc})."
        ) from exc
    return RepoPreview(
        source="demo" if is_demo_repo(repo) else "github", writes="simulated", **data
    )


@router.post("/runs", status_code=202, response_model=RunSummary)
def create_run(body: CreateRunRequest, background: BackgroundTasks, request: Request):
    ctx = _ctx(request)
    _check_repo_allowed(ctx, body.repo)
    if body.faults and not ctx.settings.demo_mode:
        raise _err(403, "faults_disabled", "Fault injection is only available in demo mode.")
    if ctx.store.count_active_runs() >= ctx.settings.max_concurrent_runs:
        raise _err(429, "too_many_runs", "Too many runs are in progress. Try again shortly.")
    row = ctx.store.create_run(body.repo, body.faults)
    if ctx.active.acquire(row["id"]):
        background.add_task(_execute, ctx, row["id"])
    return _summary(row)


@router.get("/runs", response_model=list[RunSummary])
def list_runs(request: Request):
    return [_summary(r) for r in _ctx(request).store.list_runs()]


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run(run_id: str, request: Request):
    ctx = _ctx(request)
    was_active = ctx.active.is_active(run_id)
    return _detail(ctx, _load(ctx, run_id), was_active)


@router.get("/runs/{run_id}/timeline", response_model=list[TimelineEvent])
def timeline(run_id: str, request: Request):
    ctx = _ctx(request)
    _load(ctx, run_id)
    return [
        TimelineEvent(
            seq=e["seq"], ts=e["ts"], event=e["event"], step_name=e["step_name"], detail=e["detail"]
        )
        for e in ctx.store.audit_events(run_id)
    ]


@router.post("/runs/{run_id}/resume", status_code=202, response_model=RunSummary)
def resume(run_id: str, background: BackgroundTasks, request: Request):
    ctx = _ctx(request)
    row = _load(ctx, run_id)
    if not ctx.active.acquire(run_id):
        raise _err(409, "run_busy", "This run is already executing.")
    background.add_task(_execute, ctx, run_id)
    return _summary(row)


def _decide(ctx, run_id: str, by: str, approve: bool):
    row = _load(ctx, run_id)
    if row["status"] != RunStatus.AWAITING_APPROVAL:
        raise _err(409, "not_awaiting_approval", "This run is not waiting for approval.")
    if not ctx.active.acquire(run_id):
        raise _err(409, "run_busy", "This run is already executing.")
    try:
        if approve:
            ctx.executor.approve(run_id, by)
        else:
            ctx.executor.reject(run_id, by)
    except ApprovalRequired as exc:
        raise _err(409, "no_pending_approval", exc.reason) from exc
    except SimulatedCrash:
        ctx.store.add_audit(run_id, "process_crashed", None, {"simulated": True})
    finally:
        ctx.active.release(run_id)
    return _detail(ctx, _load(ctx, run_id))


@router.post("/runs/{run_id}/approve", response_model=RunDetail)
def approve(run_id: str, body: DecisionRequest, request: Request):
    return _decide(_ctx(request), run_id, body.by, True)


@router.post("/runs/{run_id}/reject", response_model=RunDetail)
def reject(run_id: str, body: DecisionRequest, request: Request):
    return _decide(_ctx(request), run_id, body.by, False)


MAX_SUGGESTIONS = 10


def _revise(ctx, run_id: str) -> None:
    """Background job for a plan revision. The reset already happened in the request."""
    try:
        ctx.executor.run_until_blocked(run_id)
    except SimulatedCrash:
        ctx.store.add_audit(run_id, "process_crashed", None, {"simulated": True})
    finally:
        ctx.active.release(run_id)


@router.post("/runs/{run_id}/suggest", status_code=202, response_model=RunSummary)
def suggest(run_id: str, body: SuggestionRequest, background: BackgroundTasks, request: Request):
    """Add a suggestion. The agent rebuilds the plan and re-checks everything."""
    ctx = _ctx(request)
    row = _load(ctx, run_id)
    if row["status"] != RunStatus.AWAITING_APPROVAL:
        raise _err(
            409, "not_awaiting_decision", "The plan can only be changed while it waits for you."
        )
    if len(ctx.store.suggestions_for(run_id)) >= MAX_SUGGESTIONS:
        raise _err(
            409, "too_many_suggestions", f"A run accepts at most {MAX_SUGGESTIONS} suggestions."
        )
    if not ctx.active.acquire(run_id):
        raise _err(409, "run_busy", "This run is already executing.")
    try:
        ctx.executor.prepare_revision(run_id, body.text.strip())
    except ApprovalRequired as exc:
        ctx.active.release(run_id)
        raise _err(409, "not_awaiting_decision", exc.reason) from exc
    except Exception:
        ctx.active.release(run_id)
        raise
    background.add_task(_revise, ctx, run_id)
    return _summary(_load(ctx, run_id))


@router.post("/runs/{run_id}/finish", response_model=RunDetail)
def finish_self_managed(run_id: str, body: DecisionRequest, request: Request):
    """The owner will implement the plan themselves. Ends the flow; nothing is published."""
    ctx = _ctx(request)
    row = _load(ctx, run_id)
    if row["status"] != RunStatus.AWAITING_APPROVAL:
        raise _err(409, "not_awaiting_decision", "This run is not waiting for a decision.")
    if not ctx.active.acquire(run_id):
        raise _err(409, "run_busy", "This run is already executing.")
    try:
        ctx.executor.finish_self_managed(run_id, body.by)
    except ApprovalRequired as exc:
        raise _err(409, "no_pending_decision", exc.reason) from exc
    finally:
        ctx.active.release(run_id)
    return _detail(ctx, _load(ctx, run_id))


@router.get("/runs/{run_id}/plan", response_class=PlainTextResponse)
def download_plan(run_id: str, request: Request):
    """The current plan as a Markdown file. Available as soon as a plan exists."""
    ctx = _ctx(request)
    _load(ctx, run_id)
    steps = ctx.store.get_steps(run_id)
    plan = _plan_of(steps)
    if plan is None:
        raise _err(409, "no_plan_yet", "The plan has not been created yet.")
    repo = ctx.store.get_run(run_id)["repo"]
    text = render_plan(plan, source="demo" if is_demo_repo(repo) else "github", steps=steps)
    return PlainTextResponse(
        text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{plan_filename(plan)}"'},
    )


__all__ = ["router", "ActiveRuns", "StepName"]
