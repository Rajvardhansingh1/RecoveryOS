import { formatTime } from "@/lib/format";
import type { RemoteEffect } from "@/lib/types";

const ACTION: Record<string, string> = { create_draft: "Create draft release", publish: "Publish release" };

/** What the (mock) GitHub actually did, read from its own ledger, not from the agent's claims. */
export function EffectsLedger({ effects }: { effects: RemoteEffect[] }) {
  const count = (action: string, kind: string) =>
    effects.filter((e) => e.action === action && e.kind === kind).length;
  const duplicates = effects.filter((e) => e.kind === "duplicate_suppressed").length;
  // The same action applied twice under the same key would be a real duplicate. A revised plan
  // has a new key and legitimately gets its own draft.
  const seen = new Set<string>();
  let repeated = false;
  for (const e of effects) {
    if (e.kind !== "applied") continue;
    const id = `${e.action}:${e.key}`;
    if (seen.has(id)) repeated = true;
    seen.add(id);
  }

  return (
    <section className="panel" style={{ marginTop: 24 }} aria-labelledby="ledger-h">
      <h2 id="ledger-h">Remote ledger</h2>
      <p className="hint">What the mock GitHub really did. This is the exactly-once evidence.</p>
      {effects.length === 0 ? (
        <p className="empty">No side effect has reached GitHub yet.</p>
      ) : (
        <>
          <p className="why">
            Draft applied {count("create_draft", "applied")} time(s), publish applied{" "}
            {count("publish", "applied")} time(s), {duplicates} duplicate request(s) refused by key.
          </p>
          {repeated && (
            <div className="banner banner-fail" role="alert">
              <strong>An action was applied more than once.</strong>
            </div>
          )}
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Action</th>
                  <th scope="col">Result</th>
                  <th scope="col">Time</th>
                </tr>
              </thead>
              <tbody>
                {effects.map((e, i) => (
                  <tr key={i}>
                    <td>{ACTION[e.action] ?? e.action}</td>
                    <td>{e.kind === "applied" ? "Applied" : "Refused as duplicate"}</td>
                    <td className="mono">{formatTime(e.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
