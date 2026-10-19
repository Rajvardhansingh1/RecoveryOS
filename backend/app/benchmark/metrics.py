"""Aggregation. Definitions are written out in benchmark/v1/README.md."""

import math
from statistics import median

from app.benchmark.runner import RunResult


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _merge(dicts: list[dict[str, int]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in dicts:
        for k, v in d.items():
            out[k] = out.get(k, 0) + v
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def aggregate(results: list[RunResult]) -> dict:
    n = len(results)
    recoverable = [r for r in results if r.recoverable]
    completed = [r for r in results if r.status == "completed"]
    resume = [ms for r in results for ms in r.resume_ms]
    llm_steps = sum(r.llm_steps for r in results)
    fb_steps = sum(r.fallback_steps for r in results)
    tokens = sum(r.tokens for r in results)
    return {
        "scenarios": n,
        "recoverable_scenarios": len(recoverable),
        "recovery_success_rate": _rate(
            sum(r.recovered_exactly_once for r in recoverable), len(recoverable)
        ),
        "expected_outcome_rate": _rate(sum(r.outcome_matches for r in results), n),
        "duplicate_side_effect_rate": _rate(sum(r.duplicate_effects > 0 for r in results), n),
        "duplicate_side_effects_total": sum(r.duplicate_effects for r in results),
        "steps_incorrectly_marked_complete": sum(r.incorrectly_complete for r in results),
        "scenarios_with_incorrect_complete": sum(r.incorrectly_complete > 0 for r in results),
        "approval_bypass_count": sum(r.approval_bypass for r in results),
        "crash_restarts_total": sum(r.restarts for r in results),
        "time_to_resume_ms_median": round(median(resume), 2) if resume else None,
        "time_to_resume_ms_p95": round(_p95(resume), 2) if resume else None,
        "resume_samples": len(resume),
        "llm_calls_total": sum(r.llm_calls for r in results),
        "tokens_total": tokens,
        "tokens_per_completed_workflow": round(tokens / len(completed), 1) if completed else None,
        "llm_step_share": _rate(llm_steps, llm_steps + fb_steps),
        "stuck_scenarios": sum(r.stuck for r in results),
        "models_used": _merge([r.models for r in results]),
        "fallback_reasons": _merge([r.fallback_reasons for r in results]),
    }


def summarize(results: list[RunResult]) -> dict:
    out: dict = {}
    for system in sorted({r.system for r in results}):
        mine = [r for r in results if r.system == system]
        out[system] = {
            "all": aggregate(mine),
            "dev": aggregate([r for r in mine if r.split == "dev"]),
            "heldout": aggregate([r for r in mine if r.split == "heldout"]),
        }
    return out
