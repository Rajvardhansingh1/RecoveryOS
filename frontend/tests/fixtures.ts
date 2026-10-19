import type { RunDetail, StepView } from "@/lib/types";

export function step(over: Partial<StepView> & { name: string }): StepView {
  return {
    status: "succeeded",
    attempts: 1,
    produced_by: "llm",
    provider: "openrouter",
    model: "test-model",
    fallback_reason: null,
    llm_calls: 1,
    tokens: 10,
    error_code: null,
    output: null,
    started_at: null,
    finished_at: null,
    ...over,
  };
}

export function run(over: Partial<RunDetail> = {}): RunDetail {
  return {
    id: "run-1",
    repo: "demo/acme-api",
    source: "demo",
    writes: "simulated",
    status: "awaiting_approval",
    error_code: null,
    created_at: "2026-10-20T10:00:00Z",
    updated_at: "2026-10-20T10:00:05Z",
    steps: [],
    approval: null,
    checkpoints: [],
    llm_steps: 0,
    fallback_steps: 0,
    interrupted: false,
    remote_effects: [],
    outcome: null,
    revision: 1,
    suggestions: [],
    plan_revisions: [],
    ...over,
  };
}

export const planStep = step({
  name: "create_release_plan",
  output: {
    plan: {
      tag: "v1.5.0",
      head_sha: "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
      plan_hash: "f".repeat(64),
      repo: "demo/acme-api",
      rationale: "Ready.",
      notes_markdown: "### Features\n- pagination (a1b2c3d)",
    },
    draft_ref: "mock://demo/acme-api/drafts/v1.5.0",
  },
});
