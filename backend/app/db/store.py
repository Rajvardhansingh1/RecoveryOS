"""Persistence for runs, steps, checkpoints, approvals, audit events and idempotency keys.

Every method opens its own transaction, so a simulated crash between two calls leaves exactly
what was committed. `commit_step_success` is the only place a step becomes `succeeded`: the
step row, its checkpoint and its audit event are written in one transaction.
"""

import hashlib
import json
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, create_engine, func, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from app.db import tables as t
from app.models.domain import ApprovalStatus, Fault, RunStatus, StepName, StepStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url or url.endswith("//"):
            kwargs["poolclass"] = StaticPool
        return create_engine(url, **kwargs)
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


class Store:
    def __init__(self, engine: Engine):
        self.engine = engine
        self._is_sqlite = engine.dialect.name == "sqlite"
        # SQLite shares one connection in memory mode; serialize access to avoid interleaving.
        self._lock = threading.RLock()

    def create_schema(self) -> None:
        t.metadata.create_all(self.engine)

    @contextmanager
    def tx(self):
        if self._is_sqlite:
            with self._lock, self.engine.begin() as conn:
                yield conn
        else:
            with self.engine.begin() as conn:
                yield conn

    # ---- runs ----

    def create_run(self, repo: str, faults: list[Fault]) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        now = utcnow()
        with self.tx() as conn:
            conn.execute(
                insert(t.runs).values(
                    id=run_id,
                    status=RunStatus.CREATED,
                    repo=repo,
                    faults=[f.model_dump(mode="json") for f in faults],
                    created_at=now,
                    updated_at=now,
                )
            )
            self._audit(conn, run_id, "run_created", None, {"repo": repo, "faults": len(faults)})
        return self.get_run(run_id)  # type: ignore[return-value]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self.tx() as conn:
            row = conn.execute(select(t.runs).where(t.runs.c.id == run_id)).mappings().first()
            return dict(row) if row else None

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.tx() as conn:
            rows = conn.execute(
                select(t.runs).order_by(t.runs.c.created_at.desc()).limit(limit)
            ).mappings()
            return [dict(r) for r in rows]

    def count_active_runs(self) -> int:
        with self.tx() as conn:
            return conn.execute(
                select(func.count()).select_from(t.runs).where(t.runs.c.status == RunStatus.RUNNING)
            ).scalar_one()

    def set_run_status(
        self,
        run_id: str,
        status: RunStatus,
        error_code: str | None = None,
        event: str | None = None,
    ) -> None:
        with self.tx() as conn:
            conn.execute(
                update(t.runs)
                .where(t.runs.c.id == run_id)
                .values(status=status, error_code=error_code, updated_at=utcnow())
            )
            self._audit(
                conn, run_id, event or "run_status", None, {"status": status, "error": error_code}
            )

    def consume_fault(self, run_id: str, step: StepName, kind: str) -> bool:
        """Atomically use up one injected fault. Returns True if one fired."""
        with self.tx() as conn:
            row = conn.execute(select(t.runs.c.faults).where(t.runs.c.id == run_id)).first()
            if row is None:
                return False
            faults = [dict(f) for f in row[0]]
            for f in faults:
                if f["step"] == step and f["kind"] == kind and f["count"] > 0:
                    f["count"] -= 1
                    conn.execute(update(t.runs).where(t.runs.c.id == run_id).values(faults=faults))
                    self._audit(conn, run_id, "fault_injected", step, {"kind": kind})
                    return True
            return False

    # ---- steps ----

    def get_steps(self, run_id: str) -> dict[str, dict[str, Any]]:
        with self.tx() as conn:
            rows = conn.execute(
                select(t.steps).where(t.steps.c.run_id == run_id).order_by(t.steps.c.id)
            ).mappings()
            return {r["name"]: dict(r) for r in rows}

    def begin_attempt(self, run_id: str, step: StepName) -> int:
        now = utcnow()
        with self.tx() as conn:
            row = conn.execute(
                select(t.steps.c.attempts).where(
                    (t.steps.c.run_id == run_id) & (t.steps.c.name == step)
                )
            ).first()
            if row is None:
                conn.execute(
                    insert(t.steps).values(
                        run_id=run_id,
                        name=step,
                        status=StepStatus.RUNNING,
                        attempts=1,
                        started_at=now,
                    )
                )
                attempts = 1
            else:
                attempts = row[0] + 1
                conn.execute(
                    update(t.steps)
                    .where((t.steps.c.run_id == run_id) & (t.steps.c.name == step))
                    .values(status=StepStatus.RUNNING, attempts=attempts, started_at=now)
                )
            self._audit(conn, run_id, "step_started", step, {"attempt": attempts})
            return attempts

    def reset_step(self, run_id: str, step: StepName, reason: str) -> None:
        """Forget a step's result so it must be done again. History stays in the audit log
        and in earlier checkpoints."""
        with self.tx() as conn:
            conn.execute(
                t.steps.delete().where((t.steps.c.run_id == run_id) & (t.steps.c.name == step))
            )
            self._audit(conn, run_id, "step_reset", step, {"reason": reason})

    def mark_step_failure(
        self, run_id: str, step: StepName, status: StepStatus, error_code: str, detail: str = ""
    ) -> None:
        with self.tx() as conn:
            conn.execute(
                update(t.steps)
                .where((t.steps.c.run_id == run_id) & (t.steps.c.name == step))
                .values(status=status, error_code=error_code, finished_at=utcnow())
            )
            self._audit(
                conn,
                run_id,
                "step_failed",
                step,
                {"status": status, "error": error_code, "detail": detail[:200]},
            )

    def commit_step_success(
        self,
        run_id: str,
        step: StepName,
        output: dict[str, Any],
        *,
        produced_by: str,
        provider: str | None = None,
        model: str | None = None,
        fallback_reason: str | None = None,
        llm_attempts: int = 0,
        tokens: int = 0,
        idempotency_key: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> int:
        """Write step result, checkpoint and audit event atomically. Returns checkpoint seq."""
        now = utcnow()
        with self.tx() as conn:
            conn.execute(
                update(t.steps)
                .where((t.steps.c.run_id == run_id) & (t.steps.c.name == step))
                .values(
                    status=StepStatus.SUCCEEDED,
                    output=output,
                    produced_by=produced_by,
                    provider=provider,
                    model=model,
                    fallback_reason=fallback_reason,
                    llm_attempts=llm_attempts,
                    tokens=tokens,
                    idempotency_key=idempotency_key,
                    error_code=None,
                    finished_at=now,
                )
            )
            state = {
                r["name"]: r["output"]
                for r in conn.execute(
                    select(t.steps.c.name, t.steps.c.output).where(
                        (t.steps.c.run_id == run_id) & (t.steps.c.status == StepStatus.SUCCEEDED)
                    )
                ).mappings()
            }
            seq = (
                conn.execute(
                    select(func.coalesce(func.max(t.checkpoints.c.seq), 0)).where(
                        t.checkpoints.c.run_id == run_id
                    )
                ).scalar_one()
                + 1
            )
            conn.execute(
                insert(t.checkpoints).values(
                    run_id=run_id,
                    seq=seq,
                    step_name=step,
                    state_hash=canonical_hash(state),
                    state=state,
                    created_at=now,
                )
            )
            detail = {
                "produced_by": produced_by,
                "provider": provider,
                "model": model,
                "fallback_reason": fallback_reason,
                "checkpoint": seq,
                **(extra or {}),
            }
            self._audit(conn, run_id, "step_succeeded", step, detail)
            conn.execute(update(t.runs).where(t.runs.c.id == run_id).values(updated_at=now))
            return seq

    def checkpoints_for(self, run_id: str) -> list[dict[str, Any]]:
        with self.tx() as conn:
            rows = conn.execute(
                select(t.checkpoints.c.seq, t.checkpoints.c.step_name, t.checkpoints.c.state_hash)
                .where(t.checkpoints.c.run_id == run_id)
                .order_by(t.checkpoints.c.seq)
            ).mappings()
            return [dict(r) for r in rows]

    # ---- suggestions and plan revisions ----

    def add_suggestion(self, run_id: str, text: str) -> int:
        """Record an owner suggestion. Returns its sequence number (1, 2, ...)."""
        with self.tx() as conn:
            seq = (
                conn.execute(
                    select(func.coalesce(func.max(t.suggestions.c.seq), 0)).where(
                        t.suggestions.c.run_id == run_id
                    )
                ).scalar_one()
                + 1
            )
            conn.execute(
                insert(t.suggestions).values(run_id=run_id, seq=seq, text=text, created_at=utcnow())
            )
            self._audit(conn, run_id, "suggestion_received", None, {"seq": seq, "chars": len(text)})
            return seq

    def suggestions_for(self, run_id: str) -> list[str]:
        with self.tx() as conn:
            rows = conn.execute(
                select(t.suggestions.c.text)
                .where(t.suggestions.c.run_id == run_id)
                .order_by(t.suggestions.c.seq)
            )
            return [r[0] for r in rows]

    def ensure_plan_revision(
        self, run_id: str, revision: int, plan_hash: str, plan: dict[str, Any]
    ) -> None:
        """Idempotent: one row per (run, revision)."""
        with self.tx() as conn:
            exists = conn.execute(
                select(func.count())
                .select_from(t.plan_revisions)
                .where(
                    (t.plan_revisions.c.run_id == run_id)
                    & (t.plan_revisions.c.revision == revision)
                )
            ).scalar_one()
            if exists:
                return
            conn.execute(
                insert(t.plan_revisions).values(
                    run_id=run_id,
                    revision=revision,
                    plan_hash=plan_hash,
                    plan=plan,
                    created_at=utcnow(),
                )
            )
            self._audit(
                conn,
                run_id,
                "plan_revision_saved",
                None,
                {"revision": revision, "plan_hash": plan_hash},
            )

    def plan_revisions_for(self, run_id: str) -> list[dict[str, Any]]:
        with self.tx() as conn:
            rows = conn.execute(
                select(t.plan_revisions)
                .where(t.plan_revisions.c.run_id == run_id)
                .order_by(t.plan_revisions.c.revision)
            ).mappings()
            return [dict(r) for r in rows]

    def mark_step_skipped(self, run_id: str, step: StepName, reason: str) -> None:
        now = utcnow()
        with self.tx() as conn:
            row = conn.execute(
                select(t.steps.c.id).where((t.steps.c.run_id == run_id) & (t.steps.c.name == step))
            ).first()
            values = dict(
                status=StepStatus.SKIPPED,
                produced_by="system",
                output={"skipped": True, "reason": reason},
                finished_at=now,
            )
            if row is None:
                conn.execute(
                    insert(t.steps).values(
                        run_id=run_id, name=step, attempts=0, started_at=now, **values
                    )
                )
            else:
                conn.execute(
                    update(t.steps)
                    .where((t.steps.c.run_id == run_id) & (t.steps.c.name == step))
                    .values(**values)
                )
            self._audit(conn, run_id, "step_skipped", step, {"reason": reason})

    # ---- idempotency ----

    def get_idempotent(self, key: str) -> dict[str, Any] | None:
        with self.tx() as conn:
            row = conn.execute(
                select(t.idempotency_keys.c.result).where(t.idempotency_keys.c.key == key)
            ).first()
            return row[0] if row else None

    def put_idempotent(self, key: str, run_id: str, step: StepName, result: dict[str, Any]) -> bool:
        """Record a key. Returns False if it already existed (a duplicate)."""
        try:
            with self.tx() as conn:
                conn.execute(
                    insert(t.idempotency_keys).values(
                        key=key, run_id=run_id, step_name=step, result=result, created_at=utcnow()
                    )
                )
            return True
        except IntegrityError:
            return False

    # ---- approvals ----

    def create_approval(self, run_id: str, plan_hash: str, ttl_minutes: int) -> str:
        approval_id = str(uuid.uuid4())
        now = utcnow()
        with self.tx() as conn:
            conn.execute(
                insert(t.approvals).values(
                    id=approval_id,
                    run_id=run_id,
                    plan_hash=plan_hash,
                    status=ApprovalStatus.PENDING,
                    created_at=now,
                    expires_at=now + timedelta(minutes=ttl_minutes),
                )
            )
            self._audit(
                conn,
                run_id,
                "approval_requested",
                StepName.AWAIT_APPROVAL,
                {
                    "approval_id": approval_id,
                    "plan_hash": plan_hash,
                },
            )
        return approval_id

    def latest_approval(self, run_id: str) -> dict[str, Any] | None:
        with self.tx() as conn:
            row = (
                conn.execute(
                    select(t.approvals)
                    .where(t.approvals.c.run_id == run_id)
                    .order_by(t.approvals.c.created_at.desc(), t.approvals.c.id.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if not row:
                return None
            d = dict(row)
            for k in ("created_at", "decided_at", "expires_at"):
                d[k] = aware(d[k])
            return d

    def decide_approval(
        self, run_id: str, status: ApprovalStatus, decided_by: str, ttl_minutes: int | None = None
    ) -> bool:
        """Approve/reject/revoke the latest approval. Only a pending one can be approved or
        rejected; an approved one can be revoked."""
        now = utcnow()
        with self.tx() as conn:
            row = (
                conn.execute(
                    select(t.approvals)
                    .where(t.approvals.c.run_id == run_id)
                    .order_by(t.approvals.c.created_at.desc(), t.approvals.c.id.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if row is None:
                return False
            allowed = (
                {ApprovalStatus.PENDING}
                if status
                in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.SELF_MANAGED)
                else {ApprovalStatus.APPROVED, ApprovalStatus.PENDING}
            )
            if row["status"] not in allowed:
                return False
            values: dict[str, Any] = {
                "status": status,
                "decided_by": decided_by[:100],
                "decided_at": now,
            }
            if status == ApprovalStatus.APPROVED and ttl_minutes is not None:
                values["expires_at"] = now + timedelta(minutes=ttl_minutes)
            conn.execute(update(t.approvals).where(t.approvals.c.id == row["id"]).values(**values))
            self._audit(
                conn,
                run_id,
                f"approval_{status}",
                StepName.AWAIT_APPROVAL,
                {
                    "approval_id": row["id"],
                    "by": decided_by[:100],
                },
            )
            return True

    # ---- audit ----

    def add_audit(
        self, run_id: str, event: str, step: StepName | None = None, detail: dict | None = None
    ) -> None:
        with self.tx() as conn:
            self._audit(conn, run_id, event, step, detail or {})

    def audit_events(self, run_id: str) -> list[dict[str, Any]]:
        with self.tx() as conn:
            rows = conn.execute(
                select(t.audit_events)
                .where(t.audit_events.c.run_id == run_id)
                .order_by(t.audit_events.c.seq)
            ).mappings()
            return [dict(r) for r in rows]

    @staticmethod
    def _audit(
        conn: Connection, run_id: str, event: str, step: str | None, detail: dict[str, Any]
    ) -> None:
        seq = (
            conn.execute(
                select(func.coalesce(func.max(t.audit_events.c.seq), 0)).where(
                    t.audit_events.c.run_id == run_id
                )
            ).scalar_one()
            + 1
        )
        conn.execute(
            insert(t.audit_events).values(
                run_id=run_id, seq=seq, ts=utcnow(), event=event, step_name=step, detail=detail
            )
        )

    # ---- simulated remote world (used only by the mock GitHub adapter) ----

    def remote_apply(self, run_id: str, key: str, action: str, payload: dict[str, Any]) -> bool:
        """Record an effect. Returns True if newly applied, False if the key was already used."""
        with self.tx() as conn:
            existing = conn.execute(
                select(func.count())
                .select_from(t.remote_effects)
                .where((t.remote_effects.c.key == key) & (t.remote_effects.c.kind == "applied"))
            ).scalar_one()
            kind = "duplicate_suppressed" if existing else "applied"
            conn.execute(
                insert(t.remote_effects).values(
                    run_id=run_id,
                    key=key,
                    action=action,
                    kind=kind,
                    payload=payload,
                    created_at=utcnow(),
                )
            )
            return not existing

    def remote_apply_unkeyed(self, run_id: str, action: str, payload: dict[str, Any]) -> None:
        """Apply with no deduplication, as a remote that ignores keys would."""
        with self.tx() as conn:
            conn.execute(
                insert(t.remote_effects).values(
                    run_id=run_id,
                    key=f"unkeyed-{uuid.uuid4()}",
                    action=action,
                    kind="applied",
                    payload=payload,
                    created_at=utcnow(),
                )
            )

    def remote_lookup(self, key: str) -> dict[str, Any] | None:
        with self.tx() as conn:
            row = conn.execute(
                select(t.remote_effects.c.payload).where(
                    (t.remote_effects.c.key == key) & (t.remote_effects.c.kind == "applied")
                )
            ).first()
            return row[0] if row else None

    def remote_effects_for(self, run_id: str) -> list[dict[str, Any]]:
        with self.tx() as conn:
            rows = conn.execute(
                select(t.remote_effects)
                .where(t.remote_effects.c.run_id == run_id)
                .order_by(t.remote_effects.c.id)
            ).mappings()
            return [dict(r) for r in rows]
