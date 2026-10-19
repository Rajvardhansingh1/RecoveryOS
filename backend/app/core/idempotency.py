import hashlib

from app.db.store import canonical_hash


def idempotency_key(run_id: str, step: str, payload: object) -> str:
    """Deterministic key for a side effect.

    Depends on the run, the step and the content of the action, and deliberately not on the
    attempt number: every retry of the same action presents the same key.
    """
    return hashlib.sha256(f"{run_id}|{step}|{canonical_hash(payload)}".encode()).hexdigest()[:48]
