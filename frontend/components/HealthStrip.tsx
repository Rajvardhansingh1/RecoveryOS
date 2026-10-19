"use client";

import { useEffect, useState } from "react";
import { api, describeError } from "@/lib/api";
import type { Health } from "@/lib/types";

export function HealthStrip() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .health()
      .then((h) => alive && setHealth(h))
      .catch((e) => alive && setError(describeError(e)));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="statusbar" role="status" aria-live="polite">
      <div className="shell">
        {error && (
          <span>
            <b>API</b> unavailable. {error}
          </span>
        )}
        {!error && !health && <span>Checking the API...</span>}
        {health && (
          <>
            <span>
              <b>Database</b> {health.database === "ok" ? "connected" : "unavailable"}
            </span>
            <span>
              <b>LLM</b>{" "}
              {health.llm_configured
                ? "configured, steps try the model first"
                : "not configured, every step uses the deterministic fallback"}
            </span>
            <span>
              <b>Mode</b> {health.demo_mode ? "demo (mock GitHub, fault injection on)" : "live"}
            </span>
            <span>
              <b>Real GitHub writes</b> {health.real_github_writes ? "enabled" : "off"}
            </span>
          </>
        )}
      </div>
    </div>
  );
}
