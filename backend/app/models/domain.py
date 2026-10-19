"""Typed domain models: step names, statuses, step inputs/outputs, and faults."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StepName(StrEnum):
    INSPECT_REPO = "inspect_repo"
    RUN_CHECKS = "run_checks"
    PREPARE_CHANGELOG = "prepare_changelog"
    VALIDATE_VERSION = "validate_version"
    CREATE_RELEASE_PLAN = "create_release_plan"
    AWAIT_APPROVAL = "await_approval"
    PUBLISH_RELEASE = "publish_release"


STEP_ORDER: list[StepName] = list(StepName)
SIDE_EFFECT_STEPS = {StepName.CREATE_RELEASE_PLAN, StepName.PUBLISH_RELEASE}


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    SKIPPED = "skipped"


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"
    SELF_MANAGED = "self_managed"  # the owner chose to implement the plan themselves


class Producer(StrEnum):
    """Who produced a step result. Shown to the user on every step."""

    LLM = "llm"
    FALLBACK = "deterministic_fallback"
    HUMAN = "human"
    SYSTEM = "system"


class FaultKind(StrEnum):
    CRASH_BEFORE_PERSIST = "crash_before_persist"
    CRASH_AFTER_STEP = "crash_after_step"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    INVALID_OUTPUT = "invalid_output"


class Fault(BaseModel):
    """Inject `count` occurrences of `kind` at `step`. Demo mode only."""

    model_config = ConfigDict(extra="forbid")
    step: StepName
    kind: FaultKind
    count: int = Field(default=1, ge=1, le=5)


# ---- Repository facts, always taken from the adapter, never from the LLM ----


class Commit(BaseModel):
    sha: str = Field(min_length=7, max_length=40)
    message: str = Field(max_length=500)
    author: str = Field(default="unknown", max_length=100)


class CheckResult(BaseModel):
    name: str
    exit_code: int
    log_excerpt: str = Field(default="", max_length=500)

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


class RepoSnapshot(BaseModel):
    repo: str
    default_branch: str
    head_sha: str
    current_version: str
    tags: list[str]
    commits: list[Commit]
    dirty: bool = False


# ---- Step outputs ----


class InspectOutput(BaseModel):
    snapshot: RepoSnapshot
    summary: str = Field(min_length=1, max_length=1000)
    concerns: list[str] = Field(default_factory=list, max_length=10)


class ChecksOutput(BaseModel):
    results: list[CheckResult]
    verdict: Literal["pass", "fail"]
    reasons: list[str] = Field(default_factory=list, max_length=10)


class ChangelogEntry(BaseModel):
    sha: str
    text: str = Field(min_length=1, max_length=300)
    category: Literal["breaking", "feature", "fix", "docs", "other"] = "other"


class ChangelogOutput(BaseModel):
    entries: list[ChangelogEntry]
    markdown: str = Field(min_length=1, max_length=5000)


class VersionOutput(BaseModel):
    current: str
    bump: Literal["major", "minor", "patch"]
    next_version: str
    justification: str = Field(min_length=1, max_length=500)


class ReleasePlan(BaseModel):
    repo: str
    head_sha: str
    version: str
    tag: str
    notes_markdown: str
    rationale: str = Field(min_length=1, max_length=1000)
    plan_hash: str = ""
    # Not published and not part of the hash: the owner's own words and how they were handled.
    revision: int = 1
    owner_notes: list[str] = Field(default_factory=list)
    suggestion_response: str = ""


class PlanOutput(BaseModel):
    plan: ReleasePlan
    draft_ref: str | None = None


class ApprovalOutput(BaseModel):
    approval_id: str
    plan_hash: str


class PublishOutput(BaseModel):
    release_url: str
    tag: str
    reconciled: bool = False
