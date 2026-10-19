"""Fault injection shared by the real executor and the benchmark's naive baseline, so both
systems face identical faults at identical points."""

from collections.abc import Callable
from typing import Any

from app.core.errors import SimulatedCrash, ToolRateLimited, ToolTimeout
from app.db.store import Store
from app.models.domain import FaultKind, StepName


def call_tool(store: Store, run_id: str, step: StepName, fn: Callable[[], Any]) -> Any:
    """Call the adapter with injected faults. A timeout or corrupt reply happens after the
    call returns, as in reality: the remote may already have acted."""
    if store.consume_fault(run_id, step, FaultKind.RATE_LIMIT):
        raise ToolRateLimited(retry_after=0.0)
    raw = fn()
    if store.consume_fault(run_id, step, FaultKind.TIMEOUT):
        raise ToolTimeout("response lost")
    if store.consume_fault(run_id, step, FaultKind.INVALID_OUTPUT):
        return [{"unexpected": "garbage"}] if isinstance(raw, list) else {"unexpected": "garbage"}
    return raw


def maybe_crash(store: Store, run_id: str, step: StepName, kind: FaultKind) -> None:
    if store.consume_fault(run_id, step, kind):
        raise SimulatedCrash(f"{kind} at {step}")
