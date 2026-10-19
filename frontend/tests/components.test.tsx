import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApprovalPanel } from "@/components/ApprovalPanel";
import { EffectsLedger } from "@/components/EffectsLedger";
import { HealthStrip } from "@/components/HealthStrip";
import { OutputView } from "@/components/OutputView";
import { FallbackWhy, ProducedBy } from "@/components/ProducedBy";
import { StepRail } from "@/components/StepRail";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Timeline } from "@/components/Timeline";
import { api } from "@/lib/api";
import { planStep, run, step } from "./fixtures";

afterEach(() => {
  vi.restoreAllMocks();
  delete document.documentElement.dataset.theme;
});

describe("ProducedBy", () => {
  it("labels an LLM step with provider and model", () => {
    render(<ProducedBy step={step({ name: "run_checks" })} />);
    expect(screen.getByText("LLM")).toHaveClass("tag-llm");
    expect(screen.getByText(/openrouter \/ test-model/)).toBeInTheDocument();
  });
  it("labels a fallback step and explains why in words", () => {
    const s = step({
      name: "run_checks",
      produced_by: "deterministic_fallback",
      fallback_reason: "llm_rate_limited",
    });
    render(
      <>
        <ProducedBy step={s} />
        <FallbackWhy step={s} />
      </>,
    );
    expect(screen.getByText("Fallback")).toHaveClass("tag-fallback");
    expect(screen.getByText(/rate-limited/)).toBeInTheDocument();
    expect(screen.getByText(/deterministic/i)).toBeInTheDocument();
  });
  it("shows the human and system labels, and nothing for an unproduced step", () => {
    const { rerender, container } = render(<ProducedBy step={step({ name: "x", produced_by: "human" })} />);
    expect(screen.getByText("Human")).toHaveClass("tag-human");
    rerender(<ProducedBy step={step({ name: "x", produced_by: "system" })} />);
    expect(screen.getByText("System")).toHaveClass("tag-system");
    rerender(<ProducedBy step={step({ name: "x", produced_by: null })} />);
    expect(container).toBeEmptyDOMElement();
  });
  it("shows the fallback explanation only for fallback steps", () => {
    const { container } = render(<FallbackWhy step={step({ name: "x", produced_by: "llm" })} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("StepRail", () => {
  it("shows all seven steps, with pending ones marked and the approval step waiting", () => {
    render(
      <StepRail
        run={run({
          steps: [step({ name: "inspect_repo" }), planStep],
          checkpoints: [{ seq: 1, step: "inspect_repo", state_hash: "h" }],
        })}
      />,
    );
    expect(screen.getAllByRole("heading", { level: 3 })).toHaveLength(7);
    expect(screen.getByText("checkpoint 1")).toBeInTheDocument();
    expect(screen.getByText("Waiting for a person")).toBeInTheDocument();
    expect(document.querySelector('[data-step="run_checks"]')).toHaveAttribute("data-status", "pending");
  });
  it("does not label redone steps with checkpoints from an earlier plan revision", () => {
    render(
      <StepRail
        run={run({
          status: "running",
          steps: [step({ name: "inspect_repo", status: "running" })],
          checkpoints: [
            { seq: 1, step: "inspect_repo", state_hash: "a" },
            { seq: 2, step: "run_checks", state_hash: "b" },
          ],
        })}
      />,
    );
    expect(screen.queryByText("checkpoint 1")).toBeNull();
    expect(screen.queryByText("checkpoint 2")).toBeNull();
  });
  it("shows attempts, tokens and a running indicator", () => {
    render(
      <StepRail
        run={run({
          status: "running",
          steps: [step({ name: "inspect_repo", status: "running", attempts: 2, tokens: 42 })],
        })}
      />,
    );
    expect(screen.getByText(/2 attempts, 1 LLM call, 42 tokens/)).toBeInTheDocument();
    expect(document.querySelector(".progress")).toBeInTheDocument();
  });
});

describe("OutputView", () => {
  it("renders model and repository text as text, never as HTML", () => {
    const hostile = '<img src=x onerror="window.pwned=1"><script>window.pwned=1</script>';
    const { container } = render(
      <OutputView step={step({ name: "prepare_changelog", output: { markdown: hostile } })} />,
    );
    expect(container.querySelector("img, script")).toBeNull();
    expect(container.textContent).toContain("<img src=x");
  });
  it("tolerates a malformed output object", () => {
    const { container } = render(<OutputView step={step({ name: "inspect_repo", output: { snapshot: 5 } })} />);
    expect(container.textContent).toContain("none raised");
  });
  it("renders nothing without output and falls back to JSON for unknown steps", () => {
    const { container, rerender } = render(<OutputView step={step({ name: "x", output: null })} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<OutputView step={step({ name: "mystery", output: { a: 1 } })} />);
    expect(container.textContent).toContain('"a": 1');
  });
  it("explains a recovered publish", () => {
    render(
      <OutputView
        step={step({ name: "publish_release", output: { tag: "v1", release_url: "mock://x", reconciled: true } })}
      />,
    );
    expect(screen.getByText(/already existed, so it was reused/)).toBeInTheDocument();
  });
});

describe("ApprovalPanel (your decision)", () => {
  const pending = run({
    steps: [planStep],
    approval: { id: "a", status: "pending", plan_hash: "f".repeat(64), expires_at: null, decided_by: null },
  });
  const props = () => ({
    onApprove: vi.fn().mockResolvedValue(undefined),
    onReject: vi.fn().mockResolvedValue(undefined),
    onFinish: vi.fn().mockResolvedValue(undefined),
    onSuggest: vi.fn().mockResolvedValue(undefined),
  });

  it("says nothing is published before a plan exists and offers no decision", () => {
    render(<ApprovalPanel run={run()} {...props()} />);
    expect(screen.getByText(/Nothing is published, and nothing will be unless you approve/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("offers the choices once the plan is ready, and none is forced", () => {
    render(<ApprovalPanel run={pending} {...props()} />);
    expect(screen.getByRole("button", { name: "Revise the plan" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download the plan and finish" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve and publish v1.5.0 (simulated)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Discard this plan" })).toBeInTheDocument();
    expect(screen.getByText(/nothing is written to GitHub/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download this plan" })).toHaveAttribute(
      "href",
      expect.stringMatching(/\/v1\/runs\/run-1\/plan$/),
    );
  });

  it("sends a trimmed suggestion and clears the box", async () => {
    const p = props();
    render(<ApprovalPanel run={pending} {...p} />);
    const box = screen.getByLabelText(/Your suggestion/);
    fireEvent.change(box, { target: { value: "  Call out pagination.  " } });
    fireEvent.click(screen.getByRole("button", { name: "Revise the plan" }));
    await waitFor(() => expect(p.onSuggest).toHaveBeenCalledWith("Call out pagination."));
    await waitFor(() => expect(box).toHaveValue(""));
  });

  it("does not allow an empty or tiny suggestion", () => {
    render(<ApprovalPanel run={pending} {...props()} />);
    expect(screen.getByRole("button", { name: "Revise the plan" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Your suggestion/), { target: { value: "ab" } });
    expect(screen.getByRole("button", { name: "Revise the plan" })).toBeDisabled();
  });

  it("finishing records the choice and then downloads the plan", async () => {
    const p = props();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<ApprovalPanel run={pending} {...p} />);
    fireEvent.change(screen.getByLabelText("Your name"), { target: { value: "alice" } });
    fireEvent.click(screen.getByRole("button", { name: "Download the plan and finish" }));
    await waitFor(() => expect(p.onFinish).toHaveBeenCalledWith("alice"));
    await waitFor(() => expect(click).toHaveBeenCalled());
  });

  it("does not download if finishing failed", async () => {
    const p = props();
    p.onFinish.mockRejectedValue(new Error("x"));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<ApprovalPanel run={pending} {...p} />);
    fireEvent.click(screen.getByRole("button", { name: "Download the plan and finish" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That did not work");
    expect(click).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Download the plan and finish" })).toBeEnabled();
  });

  it("approve passes the name, and an empty name blocks both finishing actions", async () => {
    const p = props();
    render(<ApprovalPanel run={pending} {...p} />);
    fireEvent.change(screen.getByLabelText("Your name"), { target: { value: "alice" } });
    fireEvent.click(screen.getByRole("button", { name: /Approve and publish/ }));
    await waitFor(() => expect(p.onApprove).toHaveBeenCalledWith("alice"));
    fireEvent.change(screen.getByLabelText("Your name"), { target: { value: "  " } });
    expect(screen.getByRole("button", { name: /Approve and publish/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Download the plan and finish" })).toBeDisabled();
  });

  it("shows earlier suggestions and how the agent handled them", () => {
    const base = planStep.output as { plan: object };
    render(
      <ApprovalPanel
        run={run({
          ...pending,
          revision: 2,
          suggestions: ["Call out pagination."],
          steps: [{ ...planStep, output: { ...base, plan: { ...base.plan, suggestion_response: "Applied it." } } }],
        })}
        {...props()}
      />,
    );
    expect(screen.getByText("Call out pagination.")).toBeInTheDocument();
    expect(screen.getByText(/Applied it\./)).toBeInTheDocument();
  });

  it("explains the finished states and keeps the plan reachable", () => {
    const approval = { id: "a", plan_hash: "h", expires_at: null, decided_by: "alice" };
    const { rerender } = render(
      <ApprovalPanel
        run={run({ status: "completed", outcome: "plan_delivered", steps: [planStep], approval: { ...approval, status: "self_managed" } })}
        {...props()}
      />,
    );
    expect(screen.getByText(/You are implementing the plan yourself/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download the plan again" })).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    rerender(
      <ApprovalPanel
        run={run({ status: "completed", outcome: "published", steps: [planStep], approval: { ...approval, status: "approved" } })}
        {...props()}
      />,
    );
    expect(screen.getByText(/published \(simulated\)/)).toBeInTheDocument();
  });

  it("tells the user the agent is working while a revision runs", () => {
    render(
      <ApprovalPanel
        run={run({ status: "running", steps: [], suggestions: ["Call out pagination."], revision: null })}
        {...props()}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("rebuilding the plan");
    expect(screen.getByText("Call out pagination.")).toBeInTheDocument(); // kept visible meanwhile
    expect(screen.queryByText(/plan is not ready yet/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Revise the plan" })).toBeNull();
  });
});

describe("EffectsLedger", () => {
  it("has an honest empty state", () => {
    render(<EffectsLedger effects={[]} />);
    expect(screen.getByText("No side effect has reached GitHub yet.")).toBeInTheDocument();
  });
  it("counts applied and refused effects", () => {
    render(
      <EffectsLedger
        effects={[
          { key: "k1", action: "create_draft", kind: "applied", created_at: "2026-10-20T10:00:00Z" },
          { key: "k2", action: "publish", kind: "applied", created_at: "2026-10-20T10:00:01Z" },
          { key: "k2", action: "publish", kind: "duplicate_suppressed", created_at: "2026-10-20T10:00:02Z" },
        ]}
      />,
    );
    expect(screen.getByText(/publish applied 1 time\(s\), 1 duplicate request\(s\) refused/)).toBeInTheDocument();
    expect(screen.queryByText("An action was applied more than once.")).toBeNull();
  });
  it("does not raise an alert for a revised plan, which has its own key and draft", () => {
    const t = "2026-10-20T10:00:00Z";
    render(
      <EffectsLedger
        effects={[
          { key: "plan1", action: "create_draft", kind: "applied", created_at: t },
          { key: "plan2", action: "create_draft", kind: "applied", created_at: t },
        ]}
      />,
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });
  it("raises an alert if anything was applied twice", () => {
    const e = { key: "k2", action: "publish", kind: "applied" as const, created_at: "2026-10-20T10:00:00Z" };
    render(<EffectsLedger effects={[e, e]} />);
    expect(screen.getByRole("alert")).toHaveTextContent("applied more than once");
  });
});

describe("Timeline", () => {
  it("has an empty state", () => {
    render(<Timeline events={[]} />);
    expect(screen.getByText("No events recorded yet.")).toBeInTheDocument();
  });
  it("lists events with details and flags injected faults", () => {
    render(
      <Timeline
        events={[
          { seq: 1, ts: "2026-10-20T10:00:00Z", event: "run_created", step_name: null, detail: { repo: "demo/acme-api" } },
          { seq: 2, ts: "2026-10-20T10:00:01Z", event: "fault_injected", step_name: "run_checks", detail: { kind: "timeout" } },
        ]}
      />,
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText(/fault_injected/)).toHaveClass("ev-fault");
    expect(screen.getByText(/kind: timeout/)).toBeInTheDocument();
  });
});

describe("HealthStrip", () => {
  it("says plainly when the LLM is not configured", async () => {
    vi.spyOn(api, "health").mockResolvedValue({
      status: "ok", database: "ok", llm_configured: false, demo_mode: true, real_github_writes: false,
    });
    render(<HealthStrip />);
    expect(await screen.findByText(/not configured, every step uses the deterministic fallback/)).toBeInTheDocument();
    expect(screen.getByText(/Real GitHub writes/).parentElement).toHaveTextContent("off");
  });
  it("shows an actionable error when the API is down", async () => {
    vi.spyOn(api, "health").mockRejectedValue(new Error("x"));
    render(<HealthStrip />);
    expect(await screen.findByText(/unavailable/)).toBeInTheDocument();
  });
});

describe("ThemeToggle", () => {
  it("applies and stores the choice, and system clears it", () => {
    render(<ThemeToggle />);
    fireEvent.click(screen.getByRole("button", { name: "Dark" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("recoveryos-theme")).toBe("dark");
    fireEvent.click(screen.getByRole("button", { name: "System" }));
    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(localStorage.getItem("recoveryos-theme")).toBeNull();
  });
  it("still works when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    render(<ThemeToggle />);
    fireEvent.click(screen.getByRole("button", { name: "Light" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(screen.getByRole("button", { name: "Light" })).toHaveAttribute("aria-pressed", "true");
  });
});
