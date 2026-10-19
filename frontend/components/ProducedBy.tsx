import { PRODUCER_LABEL, fallbackReason } from "@/lib/format";
import type { StepView } from "@/lib/types";

const CLASS = {
  llm: "tag tag-llm",
  deterministic_fallback: "tag tag-fallback",
  human: "tag tag-human",
  system: "tag tag-system",
} as const;

/** Says who produced a step result. Text and shape carry the meaning, not colour alone. */
export function ProducedBy({ step }: { step: StepView }) {
  if (!step.produced_by) return null;
  return (
    <>
      <span className={CLASS[step.produced_by]}>{PRODUCER_LABEL[step.produced_by]}</span>
      {step.produced_by === "llm" && step.model && (
        <span className="muted mono"> {step.provider} / {step.model}</span>
      )}
    </>
  );
}

export function FallbackWhy({ step }: { step: StepView }) {
  if (step.produced_by !== "deterministic_fallback") return null;
  return (
    <p className="why">
      <strong>Why not the LLM:</strong> {fallbackReason(step.fallback_reason)} The deterministic
      implementation produced this result.
    </p>
  );
}
