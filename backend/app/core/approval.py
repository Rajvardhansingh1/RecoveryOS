from datetime import datetime

from app.adapters.github import GitHubAdapter
from app.core.errors import ApprovalRequired
from app.core.steps import compute_plan_hash
from app.db.store import Store, utcnow
from app.models.domain import ApprovalStatus, ReleasePlan


def check_gate(
    store: Store,
    adapter: GitHubAdapter,
    run_id: str,
    plan: ReleasePlan,
    now: datetime | None = None,
) -> str:
    """Re-check authorization at execution time. Returns the approval id or raises.

    Deliberately recomputes the plan hash from the plan about to be published instead of
    trusting a stored value, and re-reads the repository head so a changed repo invalidates
    the approval.
    """
    now = now or utcnow()
    approval = store.latest_approval(run_id)
    if approval is None:
        raise ApprovalRequired("no approval on record")
    if approval["status"] != ApprovalStatus.APPROVED:
        raise ApprovalRequired(f"approval is {approval['status']}")
    if approval["expires_at"] is not None and approval["expires_at"] <= now:
        raise ApprovalRequired("approval expired")
    if approval["plan_hash"] != compute_plan_hash(plan):
        raise ApprovalRequired("approval is for a different plan")
    if adapter.get_head_sha(plan.repo) != plan.head_sha:
        raise ApprovalRequired("repository changed since the plan was approved")
    return approval["id"]
