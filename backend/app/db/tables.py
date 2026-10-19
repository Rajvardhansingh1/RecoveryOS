"""Schema. The same definitions create SQLite test databases; Postgres uses the SQL migration."""

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
Json = JSON().with_variant(JSONB(), "postgresql")

runs = Table(
    "runs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("status", String(32), nullable=False),
    Column("repo", String(200), nullable=False),
    Column("faults", Json, nullable=False),
    Column("error_code", String(64)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

steps = Table(
    "steps",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("attempts", Integer, nullable=False, default=0),
    Column("produced_by", String(32)),
    Column("provider", String(64)),
    Column("model", String(200)),
    Column("fallback_reason", String(200)),
    Column("llm_attempts", Integer, nullable=False, default=0),
    Column("tokens", Integer, nullable=False, default=0),
    Column("output", Json),
    Column("idempotency_key", String(64)),
    Column("error_code", String(64)),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    UniqueConstraint("run_id", "name", name="uq_steps_run_name"),
)

checkpoints = Table(
    "checkpoints",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("step_name", String(64), nullable=False),
    Column("state_hash", String(64), nullable=False),
    Column("state", Json, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("run_id", "seq", name="uq_checkpoints_run_seq"),
)

approvals = Table(
    "approvals",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
    Column("plan_hash", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("decided_by", String(100)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("decided_at", DateTime(timezone=True)),
    Column("expires_at", DateTime(timezone=True)),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("event", String(64), nullable=False),
    Column("step_name", String(64)),
    Column("detail", Json, nullable=False),
    UniqueConstraint("run_id", "seq", name="uq_audit_run_seq"),
)

suggestions = Table(
    "suggestions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("text", String(1000), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("run_id", "seq", name="uq_suggestions_run_seq"),
)

plan_revisions = Table(
    "plan_revisions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("plan_hash", String(64), nullable=False),
    Column("plan", Json, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("run_id", "revision", name="uq_plan_revisions_run_rev"),
)

idempotency_keys = Table(
    "idempotency_keys",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("run_id", String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
    Column("step_name", String(64), nullable=False),
    Column("result", Json, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# The simulated outside world (what the mock GitHub has really done). Never read by the
# executor's own bookkeeping; only through the adapter, as a real API would be.
remote_effects = Table(
    "remote_effects",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(36), nullable=False),
    Column("key", String(64), nullable=False),
    Column("action", String(64), nullable=False),
    Column("kind", String(32), nullable=False),  # applied | duplicate_suppressed
    Column("payload", Json, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

__all__ = [
    "metadata",
    "runs",
    "steps",
    "checkpoints",
    "approvals",
    "audit_events",
    "suggestions",
    "plan_revisions",
    "idempotency_keys",
    "remote_effects",
]
