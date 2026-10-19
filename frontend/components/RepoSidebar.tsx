"use client";

import { useEffect, useState } from "react";
import { api, describeError } from "@/lib/api";
import type { RepoPreview, RepoSource } from "@/lib/types";

export interface Selection {
  repo: string;
  source: RepoSource;
}

/** Shows what the agent will read for the chosen repository, before any run starts. */
export function RepoSidebar({ selection }: { selection: Selection | null }) {
  const [data, setData] = useState<RepoPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!selection) {
      setData(null);
      setError(null);
      return;
    }
    let alive = true;
    setLoading(true);
    setError(null);
    api
      .repoPreview(selection.repo)
      .then((d) => alive && setData(d))
      .catch((e) => {
        if (!alive) return;
        setData(null);
        setError(describeError(e));
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [selection]);

  return (
    <aside className="repo-side" aria-labelledby="repo-side-h" aria-live="polite" aria-busy={loading}>
      <h2 id="repo-side-h">What the agent will read</h2>

      {!selection && (
        <p className="empty">
          Enter a public repository as owner/name and choose Preview to see its latest version, the
          commits since the last tag and its CI checks.
        </p>
      )}
      {selection && loading && <div className="skeleton" aria-label="Loading repository" />}
      {selection && error && (
        <div className="banner banner-fail" role="alert">
          <strong>Cannot read {selection.repo}.</strong>
          {error}
        </div>
      )}

      {data && !loading && (
        <>
          <p className="why">
            <strong>{data.source === "demo" ? "Demo repository (mock)" : "GitHub, read-only"}</strong>
            {". "}
            {data.source === "demo"
              ? "These are built-in fixtures, not a real repository."
              : "Read live from public GitHub."}{" "}
            Publishing is simulated: nothing is written to this repository.
          </p>
          <dl className="facts">
            <dt>Repository</dt>
            <dd className="mono">{data.repo}</dd>
            <dt>Branch</dt>
            <dd className="mono">{data.default_branch}</dd>
            <dt>Head</dt>
            <dd className="mono">{data.head_sha.slice(0, 12)}</dd>
            <dt>Version</dt>
            <dd className="mono">{data.current_version}</dd>
            <dt>Tags</dt>
            <dd className="mono">
              {data.tags.length === 0
                ? "none"
                : data.tags.slice(0, 6).join(", ") +
                  (data.tags.length > 6 ? `, +${data.tags.length - 6} more` : "")}
            </dd>
          </dl>

          <h3>
            Commits since {data.current_version} ({data.commits.length})
          </h3>
          {data.commits.length === 0 ? (
            <p className="empty">
              No commits since the last release tag, so there is nothing to release. A run on this
              repository will stop at the first step.
            </p>
          ) : (
            <ul className="commits">
              {data.commits.map((c) => (
                <li key={c.sha}>
                  <span className="mono muted">{c.sha}</span>
                  <span>{c.message}</span>
                  <span className="muted">{c.author}</span>
                </li>
              ))}
            </ul>
          )}

          <h3>CI checks</h3>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Check</th>
                  <th scope="col">Result</th>
                </tr>
              </thead>
              <tbody>
                {data.checks.map((c, i) => (
                  <tr key={`${c.name}-${i}`}>
                    <td className="mono">{c.name}</td>
                    <td>
                      {c.exit_code === 0 ? "Passed" : "Not passed"}
                      {c.log_excerpt ? <span className="muted"> ({c.log_excerpt})</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </aside>
  );
}
