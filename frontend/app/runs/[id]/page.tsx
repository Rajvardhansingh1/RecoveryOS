"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import { ApprovalPanel } from "@/components/ApprovalPanel";
import { EffectsLedger } from "@/components/EffectsLedger";
import { StepRail } from "@/components/StepRail";
import { Timeline } from "@/components/Timeline";
import { ApiError, api, describeError } from "@/lib/api";
import { isActive, llmSummary, shortHash, statusLabel } from "@/lib/format";
import type { RunDetail, TimelineEvent } from "@/lib/types";

const POLL_MS = 1500;

const FAILURE_TEXT: Record<string, string> = {
  checks_failed: "The checks failed, so the run stopped before any write.",
  retries_exhausted: "A step kept failing and the retry limit was reached. Nothing further was attempted.",
  version_conflict: "The next version already exists as a tag.",
  stale_plan: "The repository changed after the plan was approved, so the approval no longer applies.",
  nothing_to_release: "There are no commits since the last release tag, so there is nothing to release.",
  unsupported_repo: "The repository was not found, or it is private. Only public GitHub repositories can be linked.",
};

export default function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [error, setError] = useState<{ code: string; text: string } | null>(null);
  const [resuming, setResuming] = useState(false);
  const alive = useRef(true);

  const load = useCallback(async () => {
    try {
      const [r, t] = await Promise.all([api.getRun(id), api.timeline(id)]);
      if (!alive.current) return;
      setRun(r);
      setEvents(t);
      setError(null);
    } catch (e) {
      if (!alive.current) return;
      setError({ code: e instanceof ApiError ? e.code : "error", text: describeError(e) });
    }
  }, [id]);

  useEffect(() => {
    alive.current = true;
    load();
    return () => {
      alive.current = false;
    };
  }, [load]);

  const polling = run ? isActive(run.status) && !run.interrupted : !error;
  useEffect(() => {
    if (!polling) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [polling, load]);

  useEffect(() => {
    if (run) document.title = `${run.repo} ${statusLabel(run.status)} | RecoveryOS`;
  }, [run]);

  async function decide(kind: "approve" | "reject" | "finish", by: string) {
    const next = await (kind === "approve"
      ? api.approve(id, by)
      : kind === "finish"
        ? api.finish(id, by)
        : api.reject(id, by));
    setRun(next);
    setEvents(await api.timeline(id));
  }

  async function suggest(text: string) {
    await api.suggest(id, text);
    await load();
  }

  async function resume() {
    setResuming(true);
    try {
      await api.resume(id);
      await load();
    } catch (e) {
      setError({ code: e instanceof ApiError ? e.code : "error", text: describeError(e) });
    } finally {
      setResuming(false);
    }
  }

  if (error && !run) {
    const notFound = error.code === "run_not_found";
    return (
      <div className="banner banner-fail" role="alert">
        <strong>{notFound ? "This run does not exist." : "Cannot load this run."}</strong>
        {notFound ? "It may have been deleted, or the link is wrong. " : error.text + " "}
        <a href="/">Back to all runs</a>
      </div>
    );
  }
  if (!run) {
    return (
      <div aria-busy="true">
        <div className="skeleton" aria-label="Loading run" />
        <div className="skeleton" />
        <div className="skeleton" />
      </div>
    );
  }

  const sum = llmSummary(run.steps);
  const failed = run.status === "failed";

  return (
    <>
      <div className="run-head">
        <div>
          <p className="mono">{run.repo}</p>
          <p className="hint">
            {run.source === "github"
              ? "Linked GitHub repository, read live. Publishing is simulated: nothing is written to it."
              : "Demo repository on a mock GitHub."}
          </p>
          <h1>
            Run <span className={`state state-${run.status}`}>{statusLabel(run.status)}</span>
          </h1>
        </div>
        <p className="mono">{run.id}</p>
      </div>
      <p className="ledger-line">
        {sum.total === 0
          ? "No step has finished yet."
          : `Of ${sum.total} finished AI-capable steps, ${sum.llm} used the LLM and ${sum.fallback} used the deterministic fallback.`}
      </p>

      {run.interrupted && (
        <div className="banner banner-wait" role="status">
          <strong>The process stopped partway.</strong>
          Everything saved so far is intact. Resuming continues from the last checkpoint
          {run.checkpoints.length > 0 ? ` (${run.checkpoints.length})` : ""} and does not repeat finished
          steps.
          <div style={{ marginTop: 12 }}>
            <button type="button" className="btn btn-primary" onClick={resume} disabled={resuming}>
              {resuming ? "Resuming..." : "Resume run"}
            </button>
          </div>
        </div>
      )}
      {failed && (
        <div className="banner banner-fail" role="alert">
          <strong>The run stopped: {run.error_code ?? "failed"}.</strong>
          {FAILURE_TEXT[run.error_code ?? ""] ?? "See the timeline for the step that failed."}
        </div>
      )}
      {run.status === "rejected" && (
        <div className="banner" role="status">
          <strong>Rejected.</strong>Nothing was published.
        </div>
      )}
      {error && run && (
        <div className="banner banner-fail" role="alert">
          <strong>Refresh failed.</strong>
          {error.text}
        </div>
      )}

      <div className="run-grid">
        <section aria-labelledby="steps-h">
          <h2 id="steps-h" className="sr-only">
            Steps
          </h2>
          <StepRail run={run} />
        </section>
        <aside className="side" aria-label="Decision and evidence">
          <ApprovalPanel
            run={run}
            onApprove={(by) => decide("approve", by)}
            onReject={(by) => decide("reject", by)}
            onFinish={(by) => decide("finish", by)}
            onSuggest={suggest}
          />
          <EffectsLedger effects={run.remote_effects} />
        </aside>
      </div>

      <section className="section" aria-labelledby="cp-h">
        <h2 id="cp-h">Checkpoints</h2>
        {run.checkpoints.length === 0 ? (
          <p className="empty">No checkpoint yet. The first is saved when the first step finishes.</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col" className="num">
                    #
                  </th>
                  <th scope="col">After step</th>
                  <th scope="col">State hash</th>
                </tr>
              </thead>
              <tbody>
                {run.checkpoints.map((c) => (
                  <tr key={c.seq}>
                    <td className="num">{c.seq}</td>
                    <td className="mono">{c.step}</td>
                    <td className="mono">{shortHash(c.state_hash, 16)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="section" aria-labelledby="tl-h">
        <h2 id="tl-h">Audit timeline</h2>
        <Timeline events={events} />
      </section>
    </>
  );
}
