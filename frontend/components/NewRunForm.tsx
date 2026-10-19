"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { Selection } from "@/components/RepoSidebar";
import { api, describeError } from "@/lib/api";
import type { FaultInput, Health, RepoSource } from "@/lib/types";

interface FaultOption {
  id: string;
  label: string;
  expect: string;
  fault: FaultInput;
}

export const FAULT_OPTIONS: FaultOption[] = [
  {
    id: "crash-after-checks",
    label: "Crash after the checks are saved",
    expect: "The run stops. Resuming must not run the checks again.",
    fault: { step: "run_checks", kind: "crash_after_step", count: 1 },
  },
  {
    id: "crash-after-draft",
    label: "Crash after the draft release is created, before it is recorded",
    expect: "GitHub has the draft but we do not. Resuming must find it, not create a second one.",
    fault: { step: "create_release_plan", kind: "crash_before_persist", count: 1 },
  },
  {
    id: "lost-publish-reply",
    label: "Lose the reply to the publish call",
    expect: "The release is published but the answer is lost. The retry must not publish twice.",
    fault: { step: "publish_release", kind: "timeout", count: 1 },
  },
  {
    id: "crash-after-publish",
    label: "Crash after publishing, before it is recorded",
    expect: "Resuming must see the release exists and must not publish again.",
    fault: { step: "publish_release", kind: "crash_before_persist", count: 1 },
  },
  {
    id: "rate-limit-checks",
    label: "Rate limit the checks twice in a row",
    expect: "Two retries, then success on the third attempt.",
    fault: { step: "run_checks", kind: "rate_limit", count: 2 },
  },
  {
    id: "garbage-inspect",
    label: "Return malformed data when inspecting the repository",
    expect: "The reply is rejected and retried, never accepted as a result.",
    fault: { step: "inspect_repo", kind: "invalid_output", count: 1 },
  },
];

const GITHUB_NAME = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\/[A-Za-z0-9._-]{1,100}$/;

/** Same rule as the server: owner/name, and the name may not be "." or "..". */
function validRepo(value: string): boolean {
  return GITHUB_NAME.test(value) && !/\/\.{1,2}$/.test(value);
}

interface Props {
  onSelect?: (selection: Selection | null) => void;
}

export function NewRunForm({ onSelect }: Props) {
  const router = useRouter();
  const [demoRepos, setDemoRepos] = useState<string[] | null>(null);
  const [linkEnabled, setLinkEnabled] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [source, setSource] = useState<RepoSource>("demo");
  const [demoRepo, setDemoRepo] = useState("");
  const [ghRepo, setGhRepo] = useState("");
  const [previewed, setPreviewed] = useState<string | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all([api.repos(), api.health()])
      .then(([r, h]) => {
        if (!alive) return;
        setDemoRepos(r.repos);
        setLinkEnabled(r.github_link);
        setHealth(h);
        const first = r.repos[0] ?? "";
        setDemoRepo(first);
        if (first) onSelect?.({ repo: first, source: "demo" });
      })
      .catch((e) => alive && setLoadError(describeError(e)));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const ghValid = validRepo(ghRepo.trim());
  const repo = source === "demo" ? demoRepo : ghRepo.trim();
  const canStart = source === "demo" ? demoRepo !== "" : ghValid;

  function pickSource(next: RepoSource) {
    setSource(next);
    setError(null);
    if (next === "demo") onSelect?.(demoRepo ? { repo: demoRepo, source: "demo" } : null);
    else onSelect?.(previewed ? { repo: previewed, source: "github" } : null);
  }

  function pickDemo(value: string) {
    setDemoRepo(value);
    onSelect?.({ repo: value, source: "demo" });
  }

  function preview() {
    const value = ghRepo.trim();
    if (!validRepo(value)) return;
    setPreviewed(value);
    onSelect?.({ repo: value, source: "github" });
  }

  function toggle(id: string) {
    setChosen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canStart) return;
    setBusy(true);
    setError(null);
    try {
      const faults = FAULT_OPTIONS.filter((o) => chosen.has(o.id)).map((o) => o.fault);
      const run = await api.createRun(repo, faults);
      router.push(`/runs/${run.id}`);
    } catch (err) {
      setError(describeError(err));
      setBusy(false);
    }
  }

  return (
    <form className="panel" onSubmit={submit} aria-labelledby="new-run-h">
      <h2 id="new-run-h">New release run</h2>

      {loadError && (
        <div className="banner banner-fail" role="alert">
          <strong>Cannot load the form.</strong>
          {loadError}
        </div>
      )}
      {!loadError && !demoRepos && <div className="skeleton" aria-label="Loading repositories" />}

      {demoRepos && (
        <>
          <fieldset className="source">
            <legend className="label">Repository source</legend>
            <label className="choice">
              <input
                type="radio"
                name="source"
                checked={source === "demo"}
                onChange={() => pickSource("demo")}
              />
              <span>
                Demo repository
                <span className="hint">Built-in fixtures on a mock GitHub. Always available.</span>
              </span>
            </label>
            <label className={`choice ${linkEnabled ? "" : "choice-off"}`}>
              <input
                type="radio"
                name="source"
                checked={source === "github"}
                disabled={!linkEnabled}
                onChange={() => pickSource("github")}
              />
              <span>
                Link a GitHub repository
                <span className="hint">
                  {linkEnabled
                    ? "Public repositories only. The agent reads it for real; publishing stays simulated and nothing is written to GitHub."
                    : "Turned off on this server."}
                </span>
              </span>
            </label>
          </fieldset>

          {source === "demo" ? (
            <>
              <label htmlFor="repo">Repository</label>
              <select id="repo" value={demoRepo} onChange={(e) => pickDemo(e.target.value)}>
                {demoRepos.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </>
          ) : (
            <>
              <label htmlFor="gh-repo">GitHub repository (owner/name)</label>
              <div className="inline">
                <input
                  id="gh-repo"
                  type="text"
                  value={ghRepo}
                  placeholder="psf/requests"
                  autoComplete="off"
                  spellCheck={false}
                  maxLength={140}
                  aria-invalid={ghRepo !== "" && !ghValid}
                  aria-describedby="gh-hint"
                  onChange={(e) => setGhRepo(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      preview();
                    }
                  }}
                />
                <button type="button" className="btn" disabled={!ghValid} onClick={preview}>
                  Preview repository
                </button>
              </div>
              <p id="gh-hint" className="hint">
                {ghRepo !== "" && !ghValid
                  ? "Use the form owner/name, for example psf/requests."
                  : "The server reads it with its own GitHub access. Your token is never entered here."}
              </p>
            </>
          )}

          {health?.demo_mode ? (
            <fieldset style={{ border: 0, padding: 0, margin: "24px 0 0" }}>
              <legend className="label">Inject failures (optional)</legend>
              <p className="hint">Each one happens exactly once, at the point named.</p>
              {FAULT_OPTIONS.map((o) => (
                <div className="fault" key={o.id}>
                  <div className="fault-head">
                    <input
                      id={`f-${o.id}`}
                      type="checkbox"
                      checked={chosen.has(o.id)}
                      onChange={() => toggle(o.id)}
                    />
                    <label htmlFor={`f-${o.id}`}>
                      {o.label}
                      <span className="hint" style={{ display: "block", fontWeight: 400 }}>
                        {o.expect}
                      </span>
                    </label>
                  </div>
                </div>
              ))}
            </fieldset>
          ) : (
            <p className="hint" style={{ marginTop: 16 }}>
              Failure injection is only available in demo mode.
            </p>
          )}

          {error && (
            <div className="banner banner-fail" role="alert">
              <strong>Could not start the run.</strong>
              {error}
            </div>
          )}
          <div style={{ marginTop: 24 }}>
            <button type="submit" className="btn btn-primary" disabled={busy || !canStart}>
              {busy ? "Starting..." : "Start release run"}
            </button>
          </div>
        </>
      )}
    </form>
  );
}
