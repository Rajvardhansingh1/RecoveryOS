import { describe, expect, it } from "vitest";
import { fallbackReason, isActive, llmSummary, shortHash, statusLabel, stepLabel } from "@/lib/format";
import { step } from "./fixtures";

describe("fallbackReason", () => {
  it("explains known reasons in plain language", () => {
    expect(fallbackReason("llm_not_configured")).toMatch(/No LLM is configured/);
    expect(fallbackReason("llm_rate_limited")).toMatch(/rate-limited/);
  });
  it("explains a rejected model reply with the detail", () => {
    expect(fallbackReason("llm_rejected: verdict pass contradicts exit codes (fail)")).toBe(
      "The model reply contradicted the facts: verdict pass contradicts exit codes (fail)",
    );
    expect(fallbackReason("llm_rejected")).toBe("The model reply contradicted the facts.");
  });
  it("passes unknown reasons through and handles null", () => {
    expect(fallbackReason("something_new")).toBe("something_new");
    expect(fallbackReason(null)).toBe("No reason recorded.");
  });
});

describe("labels", () => {
  it("labels steps and statuses", () => {
    expect(stepLabel("inspect_repo")).toBe("Inspect repository");
    expect(stepLabel("unknown_step")).toBe("unknown_step");
    expect(statusLabel("awaiting_approval")).toBe("Awaiting your decision");
    expect(statusLabel("skipped")).toBe("Skipped");
    expect(statusLabel("failed_retryable")).toBe("Retrying");
    expect(statusLabel("completed")).toBe("Completed");
  });
  it("shortens hashes only when long", () => {
    expect(shortHash("abcdef", 10)).toBe("abcdef");
    expect(shortHash("a".repeat(40), 10)).toHaveLength(10);
  });
  it("knows which statuses keep polling", () => {
    expect(isActive("running")).toBe(true);
    expect(isActive("created")).toBe(true);
    expect(isActive("awaiting_approval")).toBe(false);
    expect(isActive("completed")).toBe(false);
  });
});

describe("llmSummary", () => {
  it("counts only AI-capable steps", () => {
    const s = llmSummary([
      step({ name: "a", produced_by: "llm" }),
      step({ name: "b", produced_by: "llm" }),
      step({ name: "c", produced_by: "deterministic_fallback" }),
      step({ name: "d", produced_by: "human" }),
      step({ name: "e", produced_by: "system" }),
    ]);
    expect(s).toEqual({ llm: 2, fallback: 1, total: 3 });
  });
  it("is zero for no steps", () => {
    expect(llmSummary([])).toEqual({ llm: 0, fallback: 0, total: 0 });
  });
});
