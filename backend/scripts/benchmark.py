"""Benchmark CLI.

    python -m scripts.benchmark                  # run, print summary
    python -m scripts.benchmark --save           # also store the result file
    python -m scripts.benchmark --write-dataset  # (re)write benchmark/v1/scenarios.json

LLM mode is `auto`: if a provider key and model list are configured in the environment the real
provider is used; otherwise every LLM step uses the deterministic fallback and the result file
says so. There is no mock model.
"""

import argparse
import json
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from app.adapters.llm import build_llm_client
from app.benchmark.dataset import (
    DATASET_VERSION,
    MAX_ATTEMPTS,
    SEED,
    build_dataset,
    to_json,
)
from app.benchmark.metrics import summarize
from app.benchmark.runner import run_scenario
from app.config import get_settings

ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = ROOT / "benchmark" / DATASET_VERSION
BENCH_LLM_CALL_CAP = 100  # per scenario; the baseline repeats work after every crash


def run(llm_mode: str, systems: list[str], repeats: int, limit: int | None, pause: float = 0.0):
    settings = get_settings()
    use_llm = llm_mode == "auto" and build_llm_client(settings, 1) is not None
    scenarios = build_dataset()[:limit]
    results = []
    for _ in range(repeats):
        for sc in scenarios:
            for system in systems:
                results.append(
                    run_scenario(
                        system,
                        sc,
                        lambda: build_llm_client(settings, BENCH_LLM_CALL_CAP) if use_llm else None,
                    )
                )
                if pause and use_llm:
                    time.sleep(pause)
    config = {
        "dataset_version": DATASET_VERSION,
        "seed": SEED,
        "scenarios": len(scenarios),
        "repeats": repeats,
        "systems": systems,
        "max_attempts": MAX_ATTEMPTS,
        "retry_delay_seconds": 0,
        "llm_mode": "llm" if use_llm else "none: every LLM step used the deterministic fallback",
        "llm_provider": settings.llm_provider if use_llm else None,
        "llm_models": settings.model_list if use_llm else [],
        "llm_max_output_tokens": settings.llm_max_output_tokens if use_llm else None,
        "llm_call_cap_per_scenario": BENCH_LLM_CALL_CAP if use_llm else 0,
        "database": "in-memory SQLite, one fresh database per scenario",
        "python": platform.python_version(),
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    return config, results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", choices=["auto", "off"], default="auto")
    ap.add_argument("--systems", default="recoveryos,naive")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--pause", type=float, default=0.0, help="seconds between scenarios (live LLM)")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--write-dataset", action="store_true")
    args = ap.parse_args(argv)

    if args.write_dataset:
        BENCH_DIR.mkdir(parents=True, exist_ok=True)
        (BENCH_DIR / "scenarios.json").write_text(
            json.dumps(to_json(build_dataset()), indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {BENCH_DIR / 'scenarios.json'}")
        return 0

    config, results = run(args.llm, args.systems.split(","), args.repeats, args.limit, args.pause)
    summary = summarize(results)
    payload = {
        "config": config,
        "summary": summary,
        "runs": [r.__dict__ for r in results],
    }
    for system, splits in summary.items():
        a = splits["all"]
        print(f"\n== {system} ({a['scenarios']} scenarios) ==")
        for key in (
            "recovery_success_rate",
            "expected_outcome_rate",
            "duplicate_side_effect_rate",
            "duplicate_side_effects_total",
            "steps_incorrectly_marked_complete",
            "approval_bypass_count",
            "time_to_resume_ms_median",
            "time_to_resume_ms_p95",
            "tokens_per_completed_workflow",
            "llm_step_share",
        ):
            print(f"  {key:38s} {a[key]}")
    print(f"\nLLM mode: {config['llm_mode']}")
    if args.save:
        out = BENCH_DIR / "results"
        out.mkdir(parents=True, exist_ok=True)
        tag = "llm" if config["llm_provider"] else "nollm"
        path = out / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{tag}.json"
        path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
        print(f"saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
