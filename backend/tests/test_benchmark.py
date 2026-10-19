"""The benchmark harness: dataset integrity, metric arithmetic, reproducibility, and that the
baseline really lacks what it is supposed to lack. These test the harness, not model quality."""

import json
from pathlib import Path

import pytest
from helpers import ScriptedLLM, good_replies

import scripts.benchmark as cli
from app.adapters.github import _FIXTURES
from app.benchmark.dataset import build_dataset, to_json
from app.benchmark.metrics import aggregate, summarize
from app.benchmark.runner import RunResult, output_valid, run_scenario

DATASET_FILE = Path(cli.BENCH_DIR) / "scenarios.json"
NO_LLM = lambda: None  # noqa: E731


@pytest.fixture(scope="module")
def all_results():
    out = []
    for sc in build_dataset():
        for system in ("recoveryos", "naive"):
            out.append(run_scenario(system, sc, NO_LLM))
    return out


def by(results, system):
    return [r for r in results if r.system == system]


# ---- dataset ----


def test_dataset_file_matches_generator():
    assert json.loads(DATASET_FILE.read_text(encoding="utf-8")) == to_json(build_dataset())


def test_dataset_ids_unique_both_splits_present_and_expectations_valid():
    ds = build_dataset()
    assert len({s.id for s in ds}) == len(ds) == 61
    assert {s.split for s in ds} == {"dev", "heldout"}
    assert {s.expected for s in ds} <= {
        "completed",
        "failed:retries_exhausted",
        "failed:checks_failed",
        "no_publish",
    }
    assert sum(s.recoverable for s in ds) == 51


def test_split_is_stable_across_calls():
    assert [s.split for s in build_dataset()] == [s.split for s in build_dataset()]


# ---- the two systems ----


def test_recoveryos_meets_every_zero_target_on_the_full_dataset(all_results):
    mine = by(all_results, "recoveryos")
    agg = aggregate(mine)
    assert agg["approval_bypass_count"] == 0
    assert agg["duplicate_side_effects_total"] == 0
    assert agg["steps_incorrectly_marked_complete"] == 0
    assert agg["recovery_success_rate"] == 1.0
    assert agg["stuck_scenarios"] == 0
    failed = [r.scenario_id for r in mine if not r.outcome_matches]
    assert failed == []


def test_baseline_lacks_what_it_is_supposed_to_lack(all_results):
    agg = aggregate(by(all_results, "naive"))
    assert agg["approval_bypass_count"] > 0  # approval not re-checked at publish
    assert agg["duplicate_side_effects_total"] > 0  # restarts and retries without keys
    assert agg["steps_incorrectly_marked_complete"] > 0  # replies accepted unvalidated
    assert agg["recovery_success_rate"] < 1.0


def test_measurements_are_reproducible(all_results):
    def stable(r: RunResult):
        d = dict(r.__dict__)
        d.pop("resume_ms")
        return d

    again = [run_scenario("recoveryos", sc, NO_LLM) for sc in build_dataset()[:12]]
    first = [stable(r) for r in by(all_results, "recoveryos")[:12]]
    assert [stable(r) for r in again] == first


def test_attacks_never_publish_under_recoveryos(all_results):
    for r in by(all_results, "recoveryos"):
        if r.expected == "no_publish":
            assert r.publish_applied == 0, r.scenario_id


def test_failing_checks_scenario_performs_no_writes(all_results):
    r = next(r for r in by(all_results, "recoveryos") if r.scenario_id.startswith("failing-checks"))
    assert r.status == "failed:checks_failed" and r.draft_applied == 0 and r.publish_applied == 0


def test_resume_time_is_measured_only_after_a_crash(all_results):
    for r in by(all_results, "recoveryos"):
        crashed = "crash" in r.scenario_id
        assert (len(r.resume_ms) > 0) == (r.restarts > 0)
        if crashed and "attack" not in r.scenario_id:
            assert r.restarts >= 1


# ---- LLM accounting (harness only) ----


def test_llm_calls_tokens_and_labels_are_counted_for_both_systems():
    commits = _FIXTURES["demo/acme-api"]["commits"]
    sc = next(s for s in build_dataset() if s.id == "acme-api:crash_after_step@create_release_plan")
    for system in ("recoveryos", "naive"):
        r = run_scenario(system, sc, lambda: ScriptedLLM(good_replies(commits)))
        assert r.llm_calls >= 5 and r.tokens >= 50 and r.llm_steps == 5 and r.fallback_steps == 0
    rec = run_scenario("recoveryos", sc, lambda: ScriptedLLM(good_replies(commits)))
    naive = run_scenario("naive", sc, lambda: ScriptedLLM(good_replies(commits)))
    # After the crash the baseline redoes every LLM step; RecoveryOS does not.
    assert naive.llm_calls > rec.llm_calls


# ---- metrics ----


def _r(**kw):
    base = dict(
        scenario_id="x",
        split="dev",
        system="s",
        expected="completed",
        status="completed",
        recoverable=True,
        recovered_exactly_once=True,
        outcome_matches=True,
        publish_applied=1,
        draft_applied=1,
        duplicate_effects=0,
        incorrectly_complete=0,
        approval_bypass=False,
        restarts=0,
        resume_ms=[],
        llm_calls=0,
        tokens=0,
        llm_steps=0,
        fallback_steps=0,
    )
    base.update(kw)
    return RunResult(**base)


def test_metric_arithmetic():
    rs = [
        _r(),
        _r(
            recovered_exactly_once=False,
            duplicate_effects=2,
            resume_ms=[10.0, 30.0],
            tokens=100,
            llm_steps=3,
            fallback_steps=1,
        ),
        _r(
            expected="no_publish",
            recoverable=False,
            recovered_exactly_once=False,
            approval_bypass=True,
            incorrectly_complete=1,
            status="failed:x",
        ),
        _r(resume_ms=[20.0], tokens=50),
    ]
    a = aggregate(rs)
    assert a["recoverable_scenarios"] == 3 and a["recovery_success_rate"] == round(2 / 3, 4)
    assert a["duplicate_side_effect_rate"] == 0.25 and a["duplicate_side_effects_total"] == 2
    assert a["approval_bypass_count"] == 1 and a["steps_incorrectly_marked_complete"] == 1
    assert a["time_to_resume_ms_median"] == 20.0 and a["time_to_resume_ms_p95"] == 30.0
    assert a["tokens_per_completed_workflow"] == 50.0  # 150 tokens / 3 completed
    assert a["llm_step_share"] == 0.75


def test_metrics_with_no_data_are_none_not_zero():
    a = aggregate([])
    assert a["recovery_success_rate"] is None and a["time_to_resume_ms_median"] is None


def test_summary_has_dev_and_heldout():
    s = summarize([_r(system="a", split="dev"), _r(system="a", split="heldout")])
    assert s["a"]["dev"]["scenarios"] == 1 and s["a"]["all"]["scenarios"] == 2


def test_output_validity_rejects_garbage_publish_and_plan():
    assert not output_valid(
        "publish_release", {"release_url": "None", "tag": "None", "reconciled": False}
    )
    assert output_valid(
        "publish_release", {"release_url": "mock://r/releases/v1", "tag": "v1", "reconciled": False}
    )
    assert not output_valid("inspect_repo", {"nope": 1})


# ---- CLI ----


def test_cli_runs_and_saves_a_result_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "BENCH_DIR", tmp_path)
    assert cli.main(["--llm", "off", "--limit", "4", "--save"]) == 0
    saved = list((tmp_path / "results").glob("*-nollm.json"))
    assert len(saved) == 1
    payload = json.loads(saved[0].read_text())
    assert payload["config"]["dataset_version"] == "v1"
    assert payload["config"]["llm_mode"].startswith("none")
    assert "recoveryos" in payload["summary"] and "naive" in payload["summary"]
    assert "approval_bypass_count" in capsys.readouterr().out


def test_models_and_fallback_reasons_are_recorded_per_system():
    commits = _FIXTURES["demo/acme-api"]["commits"]
    sc = next(s for s in build_dataset() if s.id == "acme-api:crash_after_step@run_checks")
    for system in ("recoveryos", "naive"):
        llm = run_scenario(system, sc, lambda: ScriptedLLM(good_replies(commits)))
        assert llm.models == {"scripted-1": 5} and llm.fallback_reasons == {}
        off = run_scenario(system, sc, NO_LLM)
        assert off.models == {} and off.fallback_reasons == {"llm_not_configured": 5}


def test_summary_lists_models_used_and_fallback_reasons():
    rs = [
        _r(models={"a": 3, "b": 1}, fallback_reasons={"llm_rate_limited": 1}),
        _r(models={"a": 2}, fallback_reasons={"llm_rate_limited": 2, "llm_timeout": 1}),
    ]
    agg = aggregate(rs)
    assert agg["models_used"] == {"a": 5, "b": 1}
    assert agg["fallback_reasons"] == {"llm_rate_limited": 3, "llm_timeout": 1}
