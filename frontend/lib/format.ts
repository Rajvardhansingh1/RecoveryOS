import type { Producer, StepView } from "./types";

const STEP_LABELS: Record<string, string> = {
  inspect_repo: "Inspect repository",
  run_checks: "Run checks",
  prepare_changelog: "Prepare changelog",
  validate_version: "Validate version",
  create_release_plan: "Create release plan",
  await_approval: "Your decision",
  publish_release: "Publish release",
};

export function stepLabel(name: string): string {
  return STEP_LABELS[name] ?? name;
}

const FALLBACK_REASONS: Record<string, string> = {
  llm_not_configured: "No LLM is configured on the server.",
  llm_rate_limited: "The provider rate-limited the request.",
  llm_timeout: "The provider timed out.",
  llm_unavailable: "The provider was unavailable.",
  llm_auth_error: "The provider rejected the API key.",
  llm_invalid_output: "The model reply was not valid JSON for this step.",
  llm_call_budget_exceeded: "The per-run LLM call cap was reached.",
};

export function fallbackReason(reason: string | null): string {
  if (!reason) return "No reason recorded.";
  if (reason.startsWith("llm_rejected")) {
    const detail = reason.replace(/^llm_rejected:?\s*/, "");
    return detail
      ? `The model reply contradicted the facts: ${detail}`
      : "The model reply contradicted the facts.";
  }
  return FALLBACK_REASONS[reason] ?? reason;
}

export const PRODUCER_LABEL: Record<Producer, string> = {
  llm: "LLM",
  deterministic_fallback: "Fallback",
  human: "Human",
  system: "System",
};

export function statusLabel(status: string): string {
  switch (status) {
    case "awaiting_approval":
      return "Awaiting your decision";
    case "failed_retryable":
      return "Retrying";
    case "failed_terminal":
      return "Failed";
    case "skipped":
      return "Skipped";
    default:
      return status.charAt(0).toUpperCase() + status.slice(1).replace(/_/g, " ");
  }
}

export function shortHash(hash: string, n = 10): string {
  return hash.length > n ? hash.slice(0, n) : hash;
}

export function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return (
    d.toLocaleTimeString(undefined, { hour12: false }) +
    "." +
    String(d.getMilliseconds()).padStart(3, "0")
  );
}

export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { hour12: false });
}

export function llmSummary(steps: StepView[]): { llm: number; fallback: number; total: number } {
  const eligible = steps.filter(
    (s) => s.produced_by === "llm" || s.produced_by === "deterministic_fallback",
  );
  return {
    llm: eligible.filter((s) => s.produced_by === "llm").length,
    fallback: eligible.filter((s) => s.produced_by === "deterministic_fallback").length,
    total: eligible.length,
  };
}

export function isActive(status: string): boolean {
  return status === "created" || status === "running";
}
