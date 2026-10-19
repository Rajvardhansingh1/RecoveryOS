import { FallbackWhy, ProducedBy } from "@/components/ProducedBy";
import { OutputView } from "@/components/OutputView";
import { statusLabel, stepLabel } from "@/lib/format";
import { STEP_ORDER } from "@/lib/types";
import type { RunDetail, StepView } from "@/lib/types";

function railStatus(run: RunDetail, name: string, step?: StepView): string {
  if (step) return step.status;
  if (name === "await_approval" && run.status === "awaiting_approval") return "waiting";
  return "pending";
}

export function StepRail({ run }: { run: RunDetail }) {
  const byName = new Map(run.steps.map((s) => [s.name, s]));
  const cpByStep = new Map<string, number>();
  // Only a step that currently holds a saved result has a checkpoint to show. After a plan
  // revision the steps are redone, and earlier checkpoints must not label pending steps.
  for (const c of run.checkpoints) {
    if (byName.get(c.step)?.status === "succeeded") cpByStep.set(c.step, c.seq);
  }

  return (
    <ol className="rail" aria-label="Workflow steps" style={{ listStyle: "none", padding: 0 }}>
      {STEP_ORDER.map((name) => {
        const step = byName.get(name);
        const status = railStatus(run, name, step);
        const cp = cpByStep.get(name);
        return (
          <li key={name} className="step" data-status={status} data-step={name}>
            <div className="step-head">
              <h3>{stepLabel(name)}</h3>
              {step && <ProducedBy step={step} />}
              <span className={`state state-${status}`}>
                {status === "waiting" ? "Waiting for a person" : statusLabel(status)}
              </span>
              {cp !== undefined && <span className="cp-label">checkpoint {cp}</span>}
            </div>
            <div className="step-meta mono">{name}</div>
            {step && (
              <p className="step-meta">
                {step.attempts} attempt{step.attempts === 1 ? "" : "s"}
                {step.llm_calls > 0 && `, ${step.llm_calls} LLM call${step.llm_calls === 1 ? "" : "s"}`}
                {step.tokens > 0 && `, ${step.tokens} tokens`}
                {step.error_code && step.status !== "succeeded" && `, last error ${step.error_code}`}
              </p>
            )}
            {step && <FallbackWhy step={step} />}
            {status === "running" && <div className="progress" role="presentation" />}
            {step && step.output && (
              <details>
                <summary>Show result</summary>
                <OutputView step={step} />
              </details>
            )}
          </li>
        );
      })}
    </ol>
  );
}
