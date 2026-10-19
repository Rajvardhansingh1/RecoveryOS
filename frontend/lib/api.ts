import type {
  FaultInput,
  Health,
  RepoPreview,
  RunDetail,
  RunSummary,
  TimelineEvent,
} from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);
const TIMEOUT_MS = 15_000;

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
    public retryAfter?: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      signal: ctrl.signal,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch (err) {
    const aborted = err instanceof DOMException && err.name === "AbortError";
    throw new ApiError(
      aborted ? "timeout" : "unreachable",
      aborted ? "The API took too long to answer." : "The API could not be reached.",
      0,
    );
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    let code = "http_error";
    let message = "The request failed.";
    try {
      const body = await res.json();
      code = body?.error?.code ?? code;
      message = body?.error?.message ?? message;
    } catch {
      /* non-JSON error body */
    }
    const ra = Number(res.headers.get("Retry-After"));
    throw new ApiError(code, message, res.status, Number.isFinite(ra) && ra > 0 ? ra : undefined);
  }
  return (await res.json()) as T;
}

const enc = encodeURIComponent;

/** Address of the downloadable plan file (served as an attachment). */
export const planUrl = (id: string) => `${API_URL}/v1/runs/${enc(id)}/plan`;

export const api = {
  health: () => request<Health>("/health"),
  repos: () => request<{ repos: string[]; github_link: boolean }>("/v1/repos"),
  repoPreview: (repo: string) => {
    const [owner = "", name = ""] = repo.split("/");
    return request<RepoPreview>(`/v1/repos/${enc(owner)}/${enc(name)}/preview`);
  },
  listRuns: () => request<RunSummary[]>("/v1/runs"),
  createRun: (repo: string, faults: FaultInput[]) =>
    request<RunSummary>("/v1/runs", { method: "POST", body: JSON.stringify({ repo, faults }) }),
  getRun: (id: string) => request<RunDetail>(`/v1/runs/${enc(id)}`),
  timeline: (id: string) => request<TimelineEvent[]>(`/v1/runs/${enc(id)}/timeline`),
  resume: (id: string) => request<RunSummary>(`/v1/runs/${enc(id)}/resume`, { method: "POST" }),
  approve: (id: string, by: string) =>
    request<RunDetail>(`/v1/runs/${enc(id)}/approve`, {
      method: "POST",
      body: JSON.stringify({ by }),
    }),
  suggest: (id: string, text: string) =>
    request<RunSummary>(`/v1/runs/${enc(id)}/suggest`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  finish: (id: string, by: string) =>
    request<RunDetail>(`/v1/runs/${enc(id)}/finish`, {
      method: "POST",
      body: JSON.stringify({ by }),
    }),
  reject: (id: string, by: string) =>
    request<RunDetail>(`/v1/runs/${enc(id)}/reject`, {
      method: "POST",
      body: JSON.stringify({ by }),
    }),
};

export function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "unreachable")
      return "The API could not be reached. Check that the backend is running and that NEXT_PUBLIC_API_URL points to it.";
    if (err.code === "repo_not_found")
      return "That repository was not found, or it is private. Only public GitHub repositories can be linked.";
    if (err.code === "repo_empty")
      return "This repository has no commits yet, so there is nothing to release. Push your code to it first.";
    if (err.code === "github_rate_limited")
      return "GitHub's rate limit was reached. Wait a minute and try again, or ask the server owner to set a GitHub token.";
    if (err.code === "too_many_suggestions")
      return "This run has reached its limit of suggestions. Download the plan or approve it.";
    if (err.code === "rate_limited")
      return `Too many requests. Try again in ${err.retryAfter ?? 30} seconds.`;
    return err.message;
  }
  return "Something went wrong.";
}
