# RecoveryOS benchmark v1

Run from `backend/` in the `RecoveryOS` conda env:

```
python -m scripts.benchmark --save            # run and store benchmark/v1/results/<time>-<mode>.json
python -m scripts.benchmark --repeats 3       # repeat every scenario (use with a live LLM)
python -m scripts.benchmark --llm off         # force the deterministic fallback everywhere
python -m scripts.benchmark --write-dataset   # regenerate scenarios.json (a test checks it matches)
```

## What it measures

Each scenario runs a full release workflow against the mock GitHub adapter with injected faults,
once on **RecoveryOS** and once on a **naive baseline**. Both use the same adapter, step logic,
LLM client, retry bound (3 attempts) and faults. After the run, every number is read from the
database and the simulated remote ledger, never from what a system says about itself.

The baseline lacks only what RecoveryOS adds: state kept in memory (a crash restarts from step
one), no idempotency keys, side-effect replies accepted without validation, and approval read
once at the approval step and not re-checked at publish. It is a deliberately plain baseline,
so the numbers show the value of those mechanisms, not general superiority over other systems.

## Dataset (v1, 61 scenarios)

Built from a fixed grid, with no tuning against results:

- crash after step and crash before persist at each of 6 steps, on 2 repositories (24)
- timeout (1), rate limit (2 in a row), invalid tool output (1) at each of the 4 tool-calling
  steps, on 2 repositories (24)
- retry exhaustion: a read step failing 5 times (3), which must end `retries_exhausted`
- multi-fault combinations (3)
- a repository whose checks fail, which must end `checks_failed` with no writes (1)
- approval attacks, which must never publish: approval revoked, expired, or the repository moved
  after approval; rejection; never approved; and revoke combined with a crash (6)

`split` is `dev` or `heldout` by a hash of the scenario id and the seed, fixed in
`app/benchmark/dataset.py`. The retry and gate logic was not tuned on either split.

## Metric definitions

| Metric | Definition |
|---|---|
| Recovery success rate | Of scenarios that must complete, the share that end `completed` with exactly one draft and exactly one publish applied at the remote. |
| Expected outcome rate | Share of all scenarios whose final state matches the expectation (`completed`, a named failure, or no publish). |
| Duplicate side-effect rate | Share of scenarios where the remote applied the same logical action (draft or publish) more than once. `duplicate_side_effects_total` counts the extra applications. |
| Steps incorrectly marked complete | Steps the system holds as complete whose output fails its schema or looks like garbage, or whose required side effect never reached the remote. |
| Approval bypass count | Scenarios that must not publish (approval absent, rejected, revoked, expired, or stale) in which a publish was applied. Must be 0. |
| Time to resume | Wall time from restarting after a simulated crash until the run blocks, completes or fails. Retry back-off is set to 0 in the benchmark, so this excludes sleep. The baseline's resume re-runs from step one but without database I/O or, with the LLM off, any model calls. |
| Cost per completed workflow | Total LLM tokens used across all scenarios divided by scenarios ending `completed`, counted identically for both systems, including repeated calls. No dollar figure is claimed. |
| LLM step share | Of the five LLM-capable steps in final results, the share produced by the LLM rather than the deterministic fallback. |

## Limits of this benchmark

- Scenarios and the mock remote are synthetic. Only the simulated GitHub adapter is exercised.
- The in-memory SQLite database is used for speed; Postgres behavior is not measured.
- A result file records whether the LLM was used. With the LLM off, token cost and LLM step
  share are 0 by construction and say nothing about model behavior.
- Timings come from one machine and are noisy; compare them only within one result file.
- Live-model runs vary between repeats; report the spread, not one number.
