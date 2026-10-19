"""Versioned (v1) request and response schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.domain import Fault


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    faults: list[Fault] = Field(default_factory=list, max_length=10)


class SuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=3, max_length=1000)


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    by: str = Field(default="demo-user", min_length=1, max_length=100)


class StepView(BaseModel):
    name: str
    status: str
    attempts: int
    produced_by: str | None
    provider: str | None
    model: str | None
    fallback_reason: str | None
    llm_calls: int
    tokens: int
    error_code: str | None
    output: dict[str, Any] | None
    started_at: datetime | None
    finished_at: datetime | None


class ApprovalView(BaseModel):
    id: str
    status: str
    plan_hash: str
    expires_at: datetime | None
    decided_by: str | None


class RunSummary(BaseModel):
    id: str
    repo: str
    source: str  # "demo" (fixtures) or "github" (public repository, read-only)
    writes: str  # always "simulated": nothing is written to GitHub
    status: str
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class RunDetail(RunSummary):
    steps: list[StepView]
    approval: ApprovalView | None
    checkpoints: list[dict[str, Any]]
    llm_steps: int
    fallback_steps: int
    interrupted: bool
    remote_effects: list[dict[str, Any]]
    # "published" | "plan_delivered" | "rejected" | None while the run is still open
    outcome: str | None = None
    revision: int | None = None
    suggestions: list[str] = []
    plan_revisions: list[dict[str, Any]] = []


class TimelineEvent(BaseModel):
    seq: int
    ts: datetime
    event: str
    step_name: str | None
    detail: dict[str, Any]


class ErrorBody(BaseModel):
    code: str
    message: str


class RepoPreview(BaseModel):
    source: str
    writes: str
    repo: str
    default_branch: str
    head_sha: str
    current_version: str
    tags: list[str]
    commits: list[dict[str, Any]]
    checks: list[dict[str, Any]]
