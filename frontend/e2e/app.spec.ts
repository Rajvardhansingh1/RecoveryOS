import { readFileSync } from "node:fs";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function startRun(page: Page, opts: { repo?: string; faults?: string[] } = {}) {
  await page.goto("/");
  await expect(page.getByLabel("Repository", { exact: true })).toBeVisible();
  if (opts.repo) await page.getByLabel("Repository", { exact: true }).selectOption(opts.repo);
  for (const f of opts.faults ?? []) await page.getByLabel(f, { exact: false }).check();
  await page.getByRole("button", { name: "Start release run" }).click();
  await expect(page).toHaveURL(/\/runs\//);
}

const step = (page: Page, name: string) => page.locator(`li[data-step="${name}"]`);

test.describe("main flow", () => {
  test("home explains the product and shows live health", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("not publish twice");
    await expect(page.getByRole("status").first()).toContainText("Database");
    await expect(page.getByRole("status").first()).toContainText("configured");
    await expect(page.getByRole("button", { name: "Start release run" })).toBeEnabled();
  });

  test("run waits for approval, shows LLM labels, then publishes once", async ({ page }) => {
    await startRun(page);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Awaiting your decision");
    // Five AI-capable steps, each labeled LLM.
    for (const s of ["inspect_repo", "run_checks", "prepare_changelog", "validate_version", "create_release_plan"]) {
      await expect(step(page, s).locator(".tag-llm")).toHaveText("LLM");
    }
    await expect(page.getByText("Of 5 finished AI-capable steps, 5 used the LLM and 0 used the deterministic fallback.")).toBeVisible();
    await expect(page.getByText("No side effect has reached GitHub yet.")).toBeHidden();
    await expect(page.getByText("Draft applied 1 time(s), publish applied 0 time(s)")).toBeVisible();

    await page.getByRole("button", { name: /Approve and publish v1.5.0/ }).click();
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Completed");
    await expect(page.getByText("publish applied 1 time(s)")).toBeVisible();
    await expect(step(page, "await_approval").locator(".tag-human")).toHaveText("Human");
    await expect(step(page, "publish_release").locator(".tag-system")).toHaveText("System");
    await expect(page.getByRole("list", { name: "Audit timeline" })).toContainText("run_completed");
  });

  test("a rejected LLM answer shows the fallback label and the reason", async ({ page }) => {
    // The scripted test model knows acme-api commits only, so its changelog for this repo is rejected.
    await startRun(page, { repo: "demo/breaking-change" });
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Awaiting your decision");
    const changelog = step(page, "prepare_changelog");
    await expect(changelog.locator(".tag-fallback")).toHaveText("Fallback");
    await expect(changelog).toContainText("Why not the LLM");
    await expect(changelog).toContainText("contradicted the facts");
  });

  test("reject ends the run and nothing is published", async ({ page }) => {
    await startRun(page);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Awaiting your decision");
    await page.getByRole("button", { name: "Discard this plan" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Rejected");
    await expect(page.getByText("Nothing was published.").first()).toBeVisible();
    await expect(page.getByText("publish applied 0 time(s)")).toBeVisible();
  });

  test("failing checks stop the run before any write", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Repository", { exact: true }).selectOption("demo/failing-checks");
    await page.getByRole("button", { name: "Start release run" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Failed");
    await expect(page.getByText("The checks failed, so the run stopped before any write.")).toBeVisible();
    await expect(page.getByText("No side effect has reached GitHub yet.")).toBeVisible();
  });
});

test.describe("failures", () => {
  test("crash then resume does not repeat finished steps", async ({ page }) => {
    await startRun(page, { faults: ["Crash after the checks are saved"] });
    await expect(page.getByText("The process stopped partway.")).toBeVisible();
    await expect(step(page, "inspect_repo")).toHaveAttribute("data-status", "succeeded");
    await page.getByRole("button", { name: "Resume run" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Awaiting your decision");
    await expect(step(page, "run_checks")).toContainText("1 attempt,");
    // The crash fell between two steps, so no step was left half-done: the log shows the crash
    // and the resume, and no step_started for run_checks after it.
    const log = page.getByRole("list", { name: "Audit timeline" });
    await expect(log).toContainText("process_crashed");
    await expect(log).toContainText("run_resumed");
    await expect(log.getByText("step_started · run_checks")).toHaveCount(1);
  });

  test("crash after the draft is created reuses the draft", async ({ page }) => {
    await startRun(page, { faults: ["Crash after the draft release is created"] });
    await expect(page.getByText("The process stopped partway.")).toBeVisible();
    await page.getByRole("button", { name: "Resume run" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Awaiting your decision");
    await expect(page.getByText("Draft applied 1 time(s)")).toBeVisible();
    await expect(page.getByRole("list", { name: "Audit timeline" })).toContainText("effect_reused");
  });

  test("a lost publish reply is retried without a second release", async ({ page }) => {
    await startRun(page, { faults: ["Lose the reply to the publish call"] });
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Awaiting your decision");
    await page.getByRole("button", { name: /Approve and publish v1.5.0/ }).click();
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Completed");
    await expect(page.getByText("publish applied 1 time(s)")).toBeVisible();
    await expect(step(page, "publish_release")).toContainText("2 attempts");
  });

  test("crash after publishing resumes to completed with one release", async ({ page }) => {
    await startRun(page, { faults: ["Crash after publishing, before it is recorded"] });
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Awaiting your decision");
    await page.getByRole("button", { name: /Approve and publish v1.5.0/ }).click();
    await expect(page.getByText("The process stopped partway.")).toBeVisible();
    await page.getByRole("button", { name: "Resume run" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Completed");
    await expect(page.getByText("publish applied 1 time(s)")).toBeVisible();
    await expect(step(page, "publish_release")).toContainText("Yes. The effect already existed");
  });

  test("rate limits are retried and shown", async ({ page }) => {
    await startRun(page, { faults: ["Rate limit the checks twice in a row"] });
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Awaiting your decision");
    await expect(step(page, "run_checks")).toContainText("3 attempts");
  });
});

test.describe("states and navigation", () => {
  test("unknown run shows a clear message", async ({ page }) => {
    await page.goto("/runs/does-not-exist");
    await expect(page.getByText("This run does not exist.")).toBeVisible();
  });

  test("unknown page shows the not-found page", async ({ page }) => {
    await page.goto("/nope");
    await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  });

  test("API down shows an actionable error", async ({ page }) => {
    await page.route("http://localhost:8765/**", (r) => r.abort());
    await page.goto("/");
    await expect(page.getByRole("status").first()).toContainText("could not be reached");
    await expect(page.getByText("Cannot load the form.")).toBeVisible();
  });

  test("benchmark shows the live-LLM run with its models and the no-LLM run", async ({ page }) => {
    await page.goto("/benchmark");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Benchmark");
    // Live-LLM block: says which provider and models, and which model produced how many steps.
    await expect(page.getByRole("heading", { name: /With a live LLM \(groq\)/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Which model produced these numbers" })).toBeVisible();
    await expect(page.getByText("openai/gpt-oss-120b").first()).toBeVisible();
    await expect(page.getByText(/llm_rate_limited/).first()).toBeVisible();
    // No-LLM block keeps its own plain notice.
    await expect(page.getByRole("heading", { name: "Deterministic only (no LLM)" })).toBeVisible();
    await expect(page.getByText("The LLM was off for this run.")).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "RecoveryOS" }).first()).toBeVisible();
    const bypass = page.getByRole("row", { name: /Approval bypass count/ });
    await expect(bypass).toHaveCount(2);
    await expect(bypass.first()).toContainText("0");
  });

  test("theme choice persists across reloads", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Dark" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(page.getByRole("button", { name: "Dark" })).toHaveAttribute("aria-pressed", "true");
  });

  test("keyboard: skip link works and the form is reachable", async ({ page }) => {
    await page.goto("/");
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator("#main")).toBeFocused();
  });
});

test.describe("repository sidebar and GitHub link", () => {
  test("the demo repository content is shown in the sidebar and follows the selection", async ({ page }) => {
    await page.goto("/");
    const side = page.getByRole("complementary", { name: "What the agent will read" });
    await expect(side).toContainText("Demo repository (mock)");
    await expect(side).toContainText("1.4.2");
    await expect(side).toContainText("add pagination to /orders");
    await expect(side.getByRole("cell", { name: "unit-tests" })).toBeVisible();
    await page.getByLabel("Repository", { exact: true }).selectOption("demo/breaking-change");
    await expect(side).toContainText("remove deprecated /v1/users endpoint");
    await expect(side).toContainText("Publishing is simulated");
  });

  test("a public GitHub repository can be previewed, linked and run read-only", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Link a GitHub repository").check();
    const input = page.getByLabel("GitHub repository (owner/name)");
    await input.fill("not a repo");
    await expect(page.getByText("Use the form owner/name")).toBeVisible();
    await expect(page.getByRole("button", { name: "Preview repository" })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Start release run" })).toBeDisabled();

    await input.fill("octo/hello");
    await page.getByRole("button", { name: "Preview repository" }).click();
    const side = page.getByRole("complementary", { name: "What the agent will read" });
    await expect(side).toContainText("GitHub, read-only");
    await expect(side).toContainText("Read live from public GitHub");
    await expect(side).toContainText("add search filters");
    await expect(side).toContainText("0.3.0");

    await page.getByRole("button", { name: "Start release run" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Awaiting your decision");
    await expect(page.getByText("Linked GitHub repository, read live.")).toBeVisible();
    await expect(page.getByText("0.3.0 to 0.4.0")).toBeHidden(); // collapsed until opened
    await page.goto("/");
    await expect(page.getByRole("list").filter({ hasText: "octo/hello" }).getByText("GitHub").first()).toBeVisible();
  });

  test("a missing or private repository explains that only public ones work", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Link a GitHub repository").check();
    await page.getByLabel("GitHub repository (owner/name)").fill("ghost/none");
    await page.getByRole("button", { name: "Preview repository" }).click();
    // Next.js also renders an empty role=alert route announcer, so pick ours by its text.
    const alert = page.getByRole("alert").filter({ hasText: "Cannot read ghost/none" });
    await expect(alert).toBeVisible();
    await expect(alert).toContainText("Only public GitHub repositories");
  });
});

test.describe("your decision: suggest, finish yourself, or publish", () => {
  test("suggestions rebuild the plan as many times as you like", async ({ page }) => {
    await startRun(page);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Awaiting your decision");
    await expect(page.getByRole("button", { name: "Revise the plan" })).toBeDisabled();

    await page.getByLabel(/Your suggestion/).fill("Call out the pagination change.");
    await page.getByRole("button", { name: "Revise the plan" }).click();
    await expect(page.getByText("Revision").locator("xpath=following-sibling::dd[1]")).toHaveText("2");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Awaiting your decision");
    await expect(page.getByText("Call out the pagination change.").first()).toBeVisible();
    await expect(page.getByRole("list", { name: "Audit timeline" })).toContainText("suggestion_received");

    await page.getByLabel(/Your suggestion/).fill("Also mention the checkout fix.");
    await page.getByRole("button", { name: "Revise the plan" }).click();
    await expect(page.getByText("Revision").locator("xpath=following-sibling::dd[1]")).toHaveText("3");
  });

  test("downloading the plan finishes the flow and nothing is published", async ({ page }) => {
    await startRun(page);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Awaiting your decision");
    await page.getByLabel(/Your suggestion/).fill("Reword the notes for users.");
    await page.getByRole("button", { name: "Revise the plan" }).click();
    await expect(page.getByText("Revision").locator("xpath=following-sibling::dd[1]")).toHaveText("2");

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download the plan and finish" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe("release-plan-demo-acme-api-v1.5.0-r2.md");
    const text = readFileSync((await download.path())!, "utf8");
    expect(text).toContain("# Release plan: v1.5.0 for demo/acme-api");
    expect(text).toContain("Plan revision 2");
    expect(text).toContain("Reword the notes for users.");
    expect(text).toContain("gh release create v1.5.0");

    await expect(page.getByRole("heading", { level: 1 })).toContainText("Completed");
    await expect(page.getByText("You are implementing the plan yourself.")).toBeVisible();
    await expect(step(page, "publish_release")).toHaveAttribute("data-status", "skipped");
    await expect(page.getByText("publish applied 0 time(s)")).toBeVisible();
    await expect(page.getByRole("button", { name: "Revise the plan" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Download the plan again" })).toBeVisible();
  });

  test("approving after a revision publishes once", async ({ page }) => {
    await startRun(page);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Awaiting your decision");
    await page.getByLabel(/Your suggestion/).fill("Mention the lint cleanup.");
    await page.getByRole("button", { name: "Revise the plan" }).click();
    await expect(page.getByText("Revision").locator("xpath=following-sibling::dd[1]")).toHaveText("2");
    await page.getByRole("button", { name: /Approve and publish v1.5.0/ }).click();
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Completed");
    await expect(page.getByText("publish applied 1 time(s)")).toBeVisible();
    await expect(page.getByText("The release was published (simulated).")).toBeVisible();
    await expect(page.getByText("An action was applied more than once.")).toBeHidden();
  });

  test("the plan can be downloaded without ending the run", async ({ page }) => {
    await startRun(page);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Awaiting your decision");
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("link", { name: "Download this plan" }).click(),
    ]);
    expect(download.suggestedFilename()).toBe("release-plan-demo-acme-api-v1.5.0-r1.md");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Awaiting your decision");
    await expect(page.getByRole("button", { name: "Revise the plan" })).toBeVisible();
  });
});

test.describe("accessibility and layout", () => {
  for (const theme of ["light", "dark"] as const) {
    test(`axe finds no violations in ${theme} on every screen`, async ({ page }) => {
      await page.goto("/");
      await page.evaluate((t) => localStorage.setItem("recoveryos-theme", t), theme);
      for (const path of ["/", "/benchmark"]) {
        await page.goto(path);
        await expect(page.locator("main")).toBeVisible();
        const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa", "best-practice"]).analyze();
        expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`)).toEqual([]);
      }
      await startRun(page);
      await expect(page.getByRole("heading", { level: 1 })).toContainText("Awaiting your decision");
      for (const d of await page.locator("summary").all()) await d.click();
      const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa", "best-practice"]).analyze();
      expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(" | ")}`)).toEqual([]);
    });
  }

  test("no horizontal overflow at 375px and 320px, targets are large enough", async ({ page }) => {
    for (const width of [375, 320]) {
      await page.setViewportSize({ width, height: 800 });
      await startRun(page);
      await expect(page.getByRole("heading", { level: 1 })).toContainText("Awaiting your decision");
      for (const d of await page.locator("summary").all()) await d.click();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      const small = await page.evaluate(() =>
        [...document.querySelectorAll("button, select, input[type=text]")]
          .filter((e) => (e as HTMLElement).offsetParent !== null)
          .map((e) => (e as HTMLElement).getBoundingClientRect().height)
          .filter((h) => h < 32),
      );
      expect(small).toEqual([]);
    }
  });
});

