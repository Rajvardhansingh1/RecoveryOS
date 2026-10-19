"""Benchmark dataset v1: seeded, versioned scenarios built from a fixed grid.

Construction (no hand tuning against results):
  * every applicable (step, fault kind) pair on two synthetic repositories,
  * retry-exhaustion cases, multi-fault cases,
  * approval-attack cases (approval revoked / expired / repo moved before publish, rejected,
    never granted), and one failing-check repository.
The dev/heldout split is a hash of the scenario id and the seed, fixed before any run.
"""

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Literal

DATASET_VERSION = "v1"
SEED = 20261020
MAX_ATTEMPTS = 3

Approval = Literal[
    "approve",
    "revoke_before_publish",
    "expire_before_publish",
    "head_change_before_publish",
    "reject",
    "never",
]
Expected = Literal["completed", "failed:retries_exhausted", "failed:checks_failed", "no_publish"]

CRASH_KINDS = ["crash_after_step", "crash_before_persist"]
TOOL_KINDS = {"timeout": 1, "rate_limit": 2, "invalid_output": 1}
ALL_STEPS = [
    "inspect_repo",
    "run_checks",
    "prepare_changelog",
    "validate_version",
    "create_release_plan",
    "publish_release",
]
TOOL_STEPS = ["inspect_repo", "run_checks", "create_release_plan", "publish_release"]
REPOS = ["demo/acme-api", "demo/breaking-change"]


@dataclass(frozen=True)
class Scenario:
    id: str
    repo: str
    faults: list[dict] = field(default_factory=list)
    approval: Approval = "approve"
    expected: Expected = "completed"
    split: Literal["dev", "heldout"] = "dev"

    @property
    def recoverable(self) -> bool:
        return self.expected == "completed"


def _split(scenario_id: str) -> str:
    digest = hashlib.sha256(f"{SEED}:{scenario_id}".encode()).digest()
    return "heldout" if digest[0] % 3 == 0 else "dev"


def _make(sid, repo, faults=(), approval="approve", expected="completed") -> Scenario:
    return Scenario(sid, repo, list(faults), approval, expected, _split(sid))


def build_dataset() -> list[Scenario]:
    out: list[Scenario] = []
    for repo in REPOS:
        short = repo.split("/")[1]
        for step in ALL_STEPS:
            for kind in CRASH_KINDS:
                out.append(
                    _make(
                        f"{short}:{kind}@{step}", repo, [{"step": step, "kind": kind, "count": 1}]
                    )
                )
        for step in TOOL_STEPS:
            for kind, count in TOOL_KINDS.items():
                out.append(
                    _make(
                        f"{short}:{kind}x{count}@{step}",
                        repo,
                        [{"step": step, "kind": kind, "count": count}],
                    )
                )

    repo = "demo/acme-api"
    for step, kind in [
        ("inspect_repo", "timeout"),
        ("run_checks", "rate_limit"),
        ("inspect_repo", "invalid_output"),
    ]:
        out.append(
            _make(
                f"acme-api:exhaust-{kind}@{step}",
                repo,
                [{"step": step, "kind": kind, "count": 5}],
                expected="failed:retries_exhausted",
            )
        )

    out.append(
        _make(
            "acme-api:multi-crash-plan+timeout-publish",
            repo,
            [
                {"step": "create_release_plan", "kind": "crash_before_persist", "count": 1},
                {"step": "publish_release", "kind": "timeout", "count": 1},
            ],
        )
    )
    out.append(
        _make(
            "acme-api:multi-crash-checks+ratelimit-inspect",
            repo,
            [
                {"step": "run_checks", "kind": "crash_after_step", "count": 1},
                {"step": "inspect_repo", "kind": "rate_limit", "count": 2},
            ],
        )
    )
    out.append(
        _make(
            "acme-api:multi-crash-publish+invalid-publish",
            repo,
            [
                {"step": "publish_release", "kind": "crash_before_persist", "count": 1},
                {"step": "publish_release", "kind": "invalid_output", "count": 1},
            ],
        )
    )

    out.append(
        _make("failing-checks:no-writes", "demo/failing-checks", expected="failed:checks_failed")
    )

    for approval in (
        "revoke_before_publish",
        "expire_before_publish",
        "head_change_before_publish",
        "reject",
        "never",
    ):
        out.append(
            _make(f"acme-api:attack-{approval}", repo, approval=approval, expected="no_publish")
        )
    out.append(
        _make(
            "acme-api:attack-revoke+crash-plan",
            repo,
            [{"step": "create_release_plan", "kind": "crash_after_step", "count": 1}],
            approval="revoke_before_publish",
            expected="no_publish",
        )
    )
    return out


def to_json(scenarios: list[Scenario]) -> dict:
    return {
        "dataset_version": DATASET_VERSION,
        "seed": SEED,
        "max_attempts": MAX_ATTEMPTS,
        "scenarios": [asdict(s) for s in scenarios],
    }
