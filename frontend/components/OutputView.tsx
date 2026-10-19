import type { StepView } from "@/lib/types";

type Obj = Record<string, unknown>;

const asObj = (v: unknown): Obj => (v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : {});
const asStr = (v: unknown): string => (typeof v === "string" ? v : v == null ? "" : String(v));
const asList = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <>
      <dt>{k}</dt>
      <dd>{children}</dd>
    </>
  );
}

/** Renders a step result as text. Model and repository text is never inserted as HTML. */
export function OutputView({ step }: { step: StepView }) {
  const out = asObj(step.output);
  if (!step.output) return null;

  switch (step.name) {
    case "inspect_repo": {
      const snap = asObj(out.snapshot);
      const concerns = asList(out.concerns).map(asStr);
      return (
        <div className="out">
          <dl>
            <Row k="Summary">{asStr(out.summary)}</Row>
            <Row k="Concerns">{concerns.length ? concerns.join("; ") : "none raised"}</Row>
            <Row k="Repository">
              <span className="mono">{asStr(snap.repo)}</span> at version{" "}
              <span className="mono">{asStr(snap.current_version)}</span>
            </Row>
            <Row k="Head">
              <span className="mono">{asStr(snap.head_sha)}</span>
            </Row>
            <Row k="Commits">{asList(snap.commits).length} since the last tag</Row>
          </dl>
        </div>
      );
    }
    case "run_checks": {
      const results = asList(out.results).map(asObj);
      return (
        <div className="out">
          <dl>
            <Row k="Verdict">{asStr(out.verdict)}</Row>
            <Row k="Reasons">{asList(out.reasons).map(asStr).join("; ") || "none given"}</Row>
          </dl>
          <div className="table-wrap">
            <table className="table">
              <caption className="sr-only">
                Check results
              </caption>
              <thead>
                <tr>
                  <th scope="col">Check</th>
                  <th scope="col" className="num">
                    Exit code
                  </th>
                  <th scope="col">Output</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r) => (
                  <tr key={asStr(r.name)}>
                    <td className="mono">{asStr(r.name)}</td>
                    <td className="num">{asStr(r.exit_code)}</td>
                    <td>{asStr(r.log_excerpt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      );
    }
    case "prepare_changelog":
      return (
        <div className="out">
          <pre>{asStr(out.markdown)}</pre>
        </div>
      );
    case "validate_version":
      return (
        <div className="out">
          <dl>
            <Row k="Version">
              <span className="mono">
                {asStr(out.current)} to {asStr(out.next_version)}
              </span>{" "}
              ({asStr(out.bump)} bump)
            </Row>
            <Row k="Justification">{asStr(out.justification)}</Row>
          </dl>
        </div>
      );
    case "create_release_plan": {
      const plan = asObj(out.plan);
      return (
        <div className="out">
          <dl>
            <Row k="Release">
              <span className="mono">{asStr(plan.tag)}</span> on{" "}
              <span className="mono">{asStr(plan.repo)}</span>
            </Row>
            <Row k="Commit">
              <span className="mono">{asStr(plan.head_sha)}</span>
            </Row>
            <Row k="Plan hash">
              <span className="mono">{asStr(plan.plan_hash)}</span>
            </Row>
            <Row k="Draft">
              <span className="mono">{asStr(out.draft_ref)}</span>
            </Row>
            <Row k="Rationale">{asStr(plan.rationale)}</Row>
          </dl>
          <pre>{asStr(plan.notes_markdown)}</pre>
        </div>
      );
    }
    case "await_approval":
      return (
        <div className="out">
          <dl>
            <Row k="Approved plan hash">
              <span className="mono">{asStr(out.plan_hash)}</span>
            </Row>
          </dl>
        </div>
      );
    case "publish_release":
      if (out.skipped === true)
        return (
          <div className="out">
            <p>
              Not run. {asStr(out.reason) || "The owner chose to implement the plan themselves."}
            </p>
          </div>
        );
      return (
        <div className="out">
          <dl>
            <Row k="Release">
              <span className="mono">{asStr(out.tag)}</span>
            </Row>
            <Row k="Location">
              <span className="mono">{asStr(out.release_url)}</span>
            </Row>
            <Row k="Recovered">
              {out.reconciled === true
                ? "Yes. The effect already existed, so it was reused and not repeated."
                : "No. Published once in the normal path."}
            </Row>
          </dl>
        </div>
      );
    default:
      return (
        <div className="out">
          <pre>{JSON.stringify(out, null, 2)}</pre>
        </div>
      );
  }
}
