import { formatTime } from "@/lib/format";
import type { TimelineEvent } from "@/lib/types";

const FAULT = new Set(["fault_injected", "process_crashed"]);
const BAD = new Set(["step_failed", "run_failed", "publish_blocked", "run_rejected"]);

function detailText(e: TimelineEvent): string {
  const d = e.detail;
  const parts: string[] = [];
  for (const [k, v] of Object.entries(d)) {
    if (v === null || v === undefined || v === "") continue;
    parts.push(`${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`);
  }
  return parts.join(", ");
}

export function Timeline({ events }: { events: TimelineEvent[] }) {
  if (events.length === 0) {
    return <p className="empty">No events recorded yet.</p>;
  }
  return (
    <ol className="timeline" aria-label="Audit timeline" style={{ listStyle: "none", padding: 0 }}>
      {events.map((e) => (
        <li key={e.seq}>
          <span className="mono muted">{e.seq}</span>
          <span className="mono muted time">{formatTime(e.ts)}</span>
          <span className={`ev ${FAULT.has(e.event) ? "ev-fault" : BAD.has(e.event) ? "ev-bad" : ""}`}>
            {e.event}
            {e.step_name ? ` · ${e.step_name}` : ""}
          </span>
          {detailText(e) && <span className="detail mono">{detailText(e)}</span>}
        </li>
      ))}
    </ol>
  );
}
