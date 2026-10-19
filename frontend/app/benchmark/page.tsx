import type { Metadata } from "next";
import raw from "@/data/benchmark.json";

export const metadata: Metadata = { title: "Benchmark" };

interface Agg {
  scenarios: number;
  recoverable_scenarios: number;
  recovery_success_rate: number | null;
  expected_outcome_rate: number | null;
  duplicate_side_effect_rate: number | null;
  duplicate_side_effects_total: number;
  steps_incorrectly_marked_complete: number;
  approval_bypass_count: number;
  time_to_resume_ms_median: number | null;
  time_to_resume_ms_p95: number | null;
  tokens_per_completed_workflow: number | null;
  tokens_total: number;
  llm_calls_total: number;
  llm_step_share: number | null;
  models_used?: Record<string, number>;
  fallback_reasons?: Record<string, number>;
}
interface Run {
  file: string;
  config: Record<string, unknown>;
  summary: Record<string, { all: Agg; dev: Agg; heldout: Agg }>;
}
interface Bench {
  runs: Run[];
}

const data = raw as unknown as Bench | null;

const pct = (v: number | null) => (v === null ? "n/a" : `${(v * 100).toFixed(1)}%`);
const num = (v: number | null, digits = 1) => (v === null ? "n/a" : v.toFixed(digits));
const label = (s: string) => (s === "recoveryos" ? "RecoveryOS" : s === "naive" ? "Baseline" : s);

const ROWS: { label: string; get: (a: Agg) => string; note: string }[] = [
  { label: "Recovery success rate", get: (a) => pct(a.recovery_success_rate), note: "Higher is better" },
  { label: "Expected outcome rate", get: (a) => pct(a.expected_outcome_rate), note: "Higher is better" },
  { label: "Duplicate side-effect rate", get: (a) => pct(a.duplicate_side_effect_rate), note: "Target 0" },
  { label: "Extra side effects applied", get: (a) => String(a.duplicate_side_effects_total), note: "Target 0" },
  {
    label: "Steps wrongly marked complete",
    get: (a) => String(a.steps_incorrectly_marked_complete),
    note: "Target 0",
  },
  { label: "Approval bypass count", get: (a) => String(a.approval_bypass_count), note: "Must be 0" },
  { label: "Time to resume, median (ms)", get: (a) => num(a.time_to_resume_ms_median), note: "Lower is better" },
  { label: "Time to resume, p95 (ms)", get: (a) => num(a.time_to_resume_ms_p95), note: "Lower is better" },
  { label: "LLM calls", get: (a) => String(a.llm_calls_total), note: "Counted the same for both" },
  { label: "Tokens used", get: (a) => String(a.tokens_total), note: "Counted the same for both" },
  {
    label: "Tokens per completed workflow",
    get: (a) => num(a.tokens_per_completed_workflow, 0),
    note: "Lower is better",
  },
  { label: "LLM share of AI-capable steps", get: (a) => pct(a.llm_step_share), note: "Rest used the fallback" },
];

const DEFS: [string, string][] = [
  ["Recovery success rate", "Of scenarios that must complete, the share that end completed with exactly one draft and one publish applied at the remote."],
  ["Duplicate side-effect rate", "Share of scenarios where the remote applied the same draft or publish more than once."],
  ["Steps wrongly marked complete", "Steps a system holds as complete whose output is invalid, or whose required side effect never reached the remote."],
  ["Approval bypass count", "Scenarios that must not publish (approval rejected, revoked, expired, absent or stale) in which a publish was applied."],
  ["Time to resume", "Wall time from restarting after a simulated crash until the run blocks, completes or fails. Retry back-off is 0, so sleep is excluded. With a live LLM this includes the model calls the system repeats."],
  ["Tokens per completed workflow", "Total LLM tokens across all scenarios divided by scenarios that completed. Counted the same way for both systems, including repeated calls."],
];

function modelList(m: Record<string, number> | undefined): string {
  const entries = Object.entries(m ?? {});
  if (entries.length === 0) return "none";
  return entries.map(([name, n]) => `${name} (${n} steps)`).join(", ");
}

function RunBlock({ run, index }: { run: Run; index: number }) {
  const cfg = run.config;
  const llmOff = String(cfg.llm_mode ?? "").startsWith("none");
  const systems = Object.keys(run.summary);
  const first = systems[0];
  const scenarios = first ? (run.summary[first]?.all.scenarios ?? 0) : 0;
  const id = `run-${index}`;
  const requested = (cfg.llm_models as string[] | undefined) ?? [];

  return (
    <section className="section" aria-labelledby={`${id}-h`}>
      <h2 id={`${id}-h`}>
        {llmOff ? "Deterministic only (no LLM)" : `With a live LLM (${String(cfg.llm_provider)})`}
      </h2>

      {llmOff ? (
        <div className="banner banner-wait" role="note">
          <strong>The LLM was off for this run.</strong>
          Every step used the deterministic fallback, so token cost and LLM share are 0 by
          construction and say nothing about model behaviour. The recovery numbers do not depend on
          the model.
        </div>
      ) : (
        <div className="banner" role="note">
          <strong>
            Provider {String(cfg.llm_provider)}, models tried in this order:{" "}
            <span className="mono">{requested.join(", ")}</span>
          </strong>
          If a model is rate limited or its answer fails the checks, the next one is tried, so more
          than one model can contribute. The steps each model actually produced are listed below.
        </div>
      )}

      <p className="hint" style={{ margin: "12px 0" }}>
        {scenarios} seeded failure scenarios, run once on RecoveryOS and once on a plain baseline that
        keeps state in memory, uses no idempotency keys and checks approval only once. Stored result{" "}
        <span className="mono">{run.file}</span>, dataset {String(cfg.dataset_version)}, seed{" "}
        {String(cfg.seed)}, run at {String(cfg.run_at)}.
      </p>

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Metric</th>
              {systems.map((s) => (
                <th scope="col" className="num" key={s}>
                  {label(s)}
                </th>
              ))}
              <th scope="col">Direction</th>
            </tr>
          </thead>
          <tbody>
            {ROWS.map((r) => (
              <tr key={r.label}>
                <th scope="row" style={{ fontWeight: 400, color: "inherit" }}>
                  {r.label}
                </th>
                {systems.map((s) => (
                  <td className="num" key={s}>
                    {run.summary[s] ? r.get(run.summary[s].all) : "n/a"}
                  </td>
                ))}
                <td className="muted">{r.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {!llmOff && (
        <>
          <h3 style={{ marginTop: 24 }}>Which model produced these numbers</h3>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">System</th>
                  <th scope="col">Steps produced by each model</th>
                  <th scope="col">Steps that fell back, and why</th>
                </tr>
              </thead>
              <tbody>
                {systems.map((s) => {
                  const a = run.summary[s]?.all;
                  return (
                    <tr key={s}>
                      <th scope="row" style={{ fontWeight: 400, color: "inherit" }}>
                        {label(s)}
                      </th>
                      <td className="mono">{modelList(a?.models_used)}</td>
                      <td>
                        {Object.keys(a?.fallback_reasons ?? {}).length === 0
                          ? "none"
                          : Object.entries(a?.fallback_reasons ?? {})
                              .map(([k, n]) => `${k} (${n})`)
                              .join(", ")}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="hint" style={{ marginTop: 12 }}>
            The recovery metrics measure the workflow, not the model. Model quality is not scored
            separately: each step was produced by whichever model in the chain answered first with a
            result that passed the checks.
          </p>
        </>
      )}

      <h3 style={{ marginTop: 24 }}>Recovery success rate by split</h3>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">System</th>
              <th scope="col" className="num">Dev scenarios</th>
              <th scope="col" className="num">Dev</th>
              <th scope="col" className="num">Held-out scenarios</th>
              <th scope="col" className="num">Held-out</th>
            </tr>
          </thead>
          <tbody>
            {systems.map((s) => {
              const sm = run.summary[s];
              if (!sm) return null;
              return (
                <tr key={s}>
                  <th scope="row" style={{ fontWeight: 400, color: "inherit" }}>{label(s)}</th>
                  <td className="num">{sm.dev.scenarios}</td>
                  <td className="num">{pct(sm.dev.recovery_success_rate)}</td>
                  <td className="num">{sm.heldout.scenarios}</td>
                  <td className="num">{pct(sm.heldout.recovery_success_rate)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function BenchmarkPage() {
  if (!data || data.runs.length === 0) {
    return (
      <>
        <h1>Benchmark</h1>
        <p className="empty">
          No benchmark result has been saved yet. From the backend folder run{" "}
          <code>python -m scripts.benchmark --save</code>, then rebuild this site.
        </p>
      </>
    );
  }

  return (
    <>
      <h1>Benchmark</h1>
      <p className="lede">
        The same seeded failure scenarios, measured on RecoveryOS and on a plain baseline. These are
        the stored results, not examples. Each block says whether a live model was used and which one.
      </p>

      {data.runs.map((r, i) => (
        <RunBlock key={r.file} run={r} index={i} />
      ))}

      <section className="section" aria-labelledby="def-h">
        <h2 id="def-h">What each metric means</h2>
        <dl className="defs">
          {DEFS.map(([t, d]) => (
            <div key={t}>
              <dt>{t}</dt>
              <dd>{d}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="section" aria-labelledby="cfg-h">
        <h2 id="cfg-h">How to read this</h2>
        <ul className="notes">
          <li>
            The baseline is plain on purpose. The numbers show what persistence, idempotency keys,
            reply validation and an execution-time approval check are worth, not that RecoveryOS beats
            other systems.
          </li>
          <li>
            With a live model, the baseline repeats every model call after each crash, which shows up
            in its token and time numbers. RecoveryOS does not repeat finished steps.
          </li>
          <li>
            Synthetic scenarios, one machine, in-memory SQLite, retry back-off set to 0. Timings are
            noisy and comparable only within one result. A live-model run varies from one repeat to
            the next; one run is one sample.
          </li>
        </ul>
      </section>
    </>
  );
}
