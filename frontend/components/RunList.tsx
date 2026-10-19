"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, describeError } from "@/lib/api";
import { formatDateTime, statusLabel } from "@/lib/format";
import type { RunSummary } from "@/lib/types";

export function RunList() {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .listRuns()
      .then((r) => alive && setRuns(r))
      .catch((e) => alive && setError(describeError(e)));
    return () => {
      alive = false;
    };
  }, []);

  if (error)
    return (
      <div className="banner banner-fail" role="alert">
        <strong>Cannot load runs.</strong>
        {error}
      </div>
    );
  if (!runs) return <div className="skeleton" aria-label="Loading runs" />;
  if (runs.length === 0)
    return <p className="empty">No runs yet. Start one above to see each step, its checkpoint and who produced it.</p>;

  return (
    <ul className="run-list">
      {runs.map((r) => (
        <li key={r.id}>
          <Link href={`/runs/${r.id}`}>
            <span>
              <span className="mono">{r.repo}</span>{" "}
              <span className="tag tag-system">{r.source === "github" ? "GitHub" : "Demo"}</span>
            </span>
            <span className={`state state-${r.status}`}>
              {statusLabel(r.status)}
              {r.error_code ? ` (${r.error_code})` : ""}
            </span>
            <span className="muted">{formatDateTime(r.created_at)}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
