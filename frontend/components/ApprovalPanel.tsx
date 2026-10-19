"use client";

import { useState } from "react";
import { describeError, planUrl } from "@/lib/api";
import { formatDateTime, shortHash } from "@/lib/format";
import type { RunDetail } from "@/lib/types";

interface Props {
  run: RunDetail;
  onApprove: (by: string) => Promise<void>;
  onReject: (by: string) => Promise<void>;
  onFinish: (by: string) => Promise<void>;
  onSuggest: (text: string) => Promise<void>;
}

type Obj = Record<string, unknown>;
const MAX = 1000;

function planOf(run: RunDetail) {
  const step = run.steps.find((s) => s.name === "create_release_plan");
  const plan = (step?.output as Obj | null)?.plan as Obj | undefined;
  if (!plan) return null;
  return {
    tag: String(plan.tag),
    head: String(plan.head_sha),
    hash: String(plan.plan_hash),
    notes: String(plan.notes_markdown ?? ""),
    rationale: String(plan.rationale ?? ""),
    response: String(plan.suggestion_response ?? ""),
  };
}

function download(url: string) {
  const a = document.createElement("a");
  a.href = url;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** The plan is the product. The owner decides what happens next; nothing is mandatory. */
export function ApprovalPanel({ run, onApprove, onReject, onFinish, onSuggest }: Props) {
  const [by, setBy] = useState("demo-user");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const plan = planOf(run);
  const approval = run.approval;
  const waiting = run.status === "awaiting_approval" && approval?.status === "pending";
  const done = run.status === "completed" || run.status === "rejected";
  const rebuilding = run.status === "running" && run.suggestions.length > 0;

  async function act(name: string, fn: () => Promise<void>) {
    setBusy(name);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="panel decision" aria-labelledby="decision-h">
      <h2 id="decision-h">Your decision</h2>

      {!plan && !rebuilding && (
        <p className="empty">
          The release plan is not ready yet. Nothing is published, and nothing will be unless you
          approve it.
        </p>
      )}

      {plan && (
        <>
          <dl className="facts">
            <dt>Release</dt>
            <dd className="mono">{plan.tag}</dd>
            <dt>Revision</dt>
            <dd>{run.revision ?? 1}</dd>
            <dt>Commit</dt>
            <dd className="mono">{plan.head.slice(0, 12)}</dd>
            <dt>Plan hash</dt>
            <dd className="mono">{shortHash(plan.hash, 16)}</dd>
          </dl>
          <p className="hint">
            <a href={planUrl(run.id)} download>
              Download this plan
            </a>{" "}
            (does not end the run)
          </p>
        </>
      )}

      {run.suggestions.length > 0 && (
        <div className="group">
          <h3>Your suggestions so far</h3>
          <ol className="revisions" style={{ paddingLeft: 20 }}>
            {run.suggestions.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ol>
          {plan?.response && (
            <p className="why">
              <strong>How the agent handled them:</strong> {plan.response}
            </p>
          )}
        </div>
      )}

      {waiting && plan && (
        <>
          <div className="group">
            <h3>Change the plan</h3>
            <p className="hint">
              Tell the agent what to change. It rebuilds the plan and re-checks everything, and you
              can repeat this as often as you like.
            </p>
            <label htmlFor="suggestion">
              Your suggestion <span className="char-count">{text.length}/{MAX}</span>
            </label>
            <textarea
              id="suggestion"
              value={text}
              maxLength={MAX}
              onChange={(e) => setText(e.target.value)}
              placeholder="For example: call out the pagination change as user-facing."
            />
            <button
              type="button"
              className="btn"
              disabled={busy !== null || text.trim().length < 3}
              onClick={() =>
                act("suggest", async () => {
                  await onSuggest(text.trim());
                  setText("");
                })
              }
            >
              {busy === "suggest" ? "Sending..." : "Revise the plan"}
            </button>
          </div>

          <div className="group">
            <h3>Finish</h3>
            <label htmlFor="approver">Your name</label>
            <input
              id="approver"
              type="text"
              value={by}
              maxLength={100}
              onChange={(e) => setBy(e.target.value)}
            />
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy !== null || by.trim() === ""}
              onClick={() =>
                act("finish", async () => {
                  await onFinish(by.trim());
                  download(planUrl(run.id));
                })
              }
            >
              {busy === "finish" ? "Finishing..." : "Download the plan and finish"}
            </button>
            <p className="hint">
              You implement it yourself. The plan file includes the commands. Nothing is published.
            </p>
            <button
              type="button"
              className="btn"
              disabled={busy !== null || by.trim() === ""}
              onClick={() => act("approve", () => onApprove(by.trim()))}
            >
              {busy === "approve" ? "Publishing..." : `Approve and publish ${plan.tag} (simulated)`}
            </button>
            <p className="hint">
              Publishing is simulated: nothing is written to GitHub. Approval is tied to this exact
              plan
              {approval?.expires_at ? `, expires at ${formatDateTime(approval.expires_at)}` : ""},
              and is checked again right before the release is published.
            </p>
            <button
              type="button"
              className="link-btn"
              disabled={busy !== null}
              onClick={() => act("reject", () => onReject(by.trim() || "demo-user"))}
            >
              {busy === "reject" ? "Discarding..." : "Discard this plan"}
            </button>
          </div>
        </>
      )}

      {rebuilding && (
        <p className="why" role="status">
          The agent is rebuilding the plan and re-checking everything.
        </p>
      )}

      {done && approval && (
        <div className="banner" role="status">
          <strong>
            {run.outcome === "plan_delivered"
              ? "Finished. You are implementing the plan yourself."
              : run.outcome === "published"
                ? "Finished. The release was published (simulated)."
                : "Finished. The plan was discarded."}
          </strong>
          {run.outcome === "plan_delivered" && (
            <>
              Nothing was published.{" "}
              <a href={planUrl(run.id)} download>
                Download the plan again
              </a>
              .
            </>
          )}
          {run.outcome === "published" && "Nothing was written to GitHub in this build."}
        </div>
      )}

      {!waiting && !done && approval && approval.status !== "pending" && run.status === "awaiting_approval" && (
        <p className="why">
          Latest decision is <strong>{approval.status}</strong>
          {approval.status === "revoked" && ". A revoked approval can never publish."}
        </p>
      )}

      {error && (
        <div className="banner banner-fail" role="alert">
          <strong>That did not work.</strong>
          {error}
        </div>
      )}
    </section>
  );
}
