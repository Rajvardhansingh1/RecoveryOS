export type Producer = "llm" | "deterministic_fallback" | "human" | "system";

export type RepoSource = "demo" | "github";

export interface RunSummary {
  id: string;
  repo: string;
  source: RepoSource;
  writes: "simulated";
  status: string;
  error_code: string | null;
  created_at: string;
  updated_at: string;
}

export interface StepView {
  name: string;
  status: string;
  attempts: number;
  produced_by: Producer | null;
  provider: string | null;
  model: string | null;
  fallback_reason: string | null;
  llm_calls: number;
  tokens: number;
  error_code: string | null;
  output: Record<string, unknown> | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface ApprovalView {
  id: string;
  status: string;
  plan_hash: string;
  expires_at: string | null;
  decided_by: string | null;
}

export interface Checkpoint {
  seq: number;
  step: string;
  state_hash: string;
}

export interface RemoteEffect {
  key: string;
  action: string;
  kind: "applied" | "duplicate_suppressed";
  created_at: string;
}

export interface RunDetail extends RunSummary {
  steps: StepView[];
  approval: ApprovalView | null;
  checkpoints: Checkpoint[];
  llm_steps: number;
  fallback_steps: number;
  interrupted: boolean;
  remote_effects: RemoteEffect[];
  outcome: "published" | "plan_delivered" | "rejected" | null;
  revision: number | null;
  suggestions: string[];
  plan_revisions: { revision: number; plan_hash: string; created_at: string }[];
}

export interface TimelineEvent {
  seq: number;
  ts: string;
  event: string;
  step_name: string | null;
  detail: Record<string, unknown>;
}

export interface Health {
  status: string;
  database: string;
  llm_configured: boolean;
  demo_mode: boolean;
  real_github_writes: boolean;
}

export type FaultKind =
  | "crash_after_step"
  | "crash_before_persist"
  | "timeout"
  | "rate_limit"
  | "invalid_output";

export interface FaultInput {
  step: string;
  kind: FaultKind;
  count: number;
}

export const STEP_ORDER = [
  "inspect_repo",
  "run_checks",
  "prepare_changelog",
  "validate_version",
  "create_release_plan",
  "await_approval",
  "publish_release",
] as const;

export interface RepoPreview {
  source: RepoSource;
  writes: "simulated";
  repo: string;
  default_branch: string;
  head_sha: string;
  current_version: string;
  tags: string[];
  commits: { sha: string; message: string; author: string }[];
  checks: { name: string; exit_code: number; log_excerpt: string }[];
}
