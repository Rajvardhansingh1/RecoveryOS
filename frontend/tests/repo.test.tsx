import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NewRunForm } from "@/components/NewRunForm";
import { RepoSidebar } from "@/components/RepoSidebar";
import { ApiError, api } from "@/lib/api";
import type { Health, RepoPreview } from "@/lib/types";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const preview = (over: Partial<RepoPreview> = {}): RepoPreview => ({
  source: "demo",
  writes: "simulated",
  repo: "demo/acme-api",
  default_branch: "main",
  head_sha: "a1b2c3d4e5f60718293a4b5c6d7e8f90",
  current_version: "1.4.2",
  tags: ["v1.4.0", "v1.4.1", "v1.4.2"],
  commits: [{ sha: "a1b2c3d", message: "feat: add pagination", author: "mira" }],
  checks: [
    { name: "unit-tests", exit_code: 0, log_excerpt: "212 passed" },
    { name: "lint", exit_code: 1, log_excerpt: "3 errors" },
  ],
  ...over,
});

const health = (over: Partial<Health> = {}): Health => ({
  status: "ok",
  database: "ok",
  llm_configured: true,
  demo_mode: true,
  real_github_writes: false,
  ...over,
});

beforeEach(() => push.mockReset());
afterEach(() => vi.restoreAllMocks());

describe("RepoSidebar", () => {
  it("invites the user to pick a repository when nothing is selected", () => {
    render(<RepoSidebar selection={null} />);
    expect(screen.getByText(/Enter a public repository as owner\/name/)).toBeInTheDocument();
  });

  it("shows the demo repository's version, tags, commits and checks", async () => {
    vi.spyOn(api, "repoPreview").mockResolvedValue(preview());
    render(<RepoSidebar selection={{ repo: "demo/acme-api", source: "demo" }} />);
    expect(await screen.findByText("Demo repository (mock)")).toBeInTheDocument();
    expect(screen.getByText("1.4.2")).toBeInTheDocument();
    expect(screen.getByText("v1.4.0, v1.4.1, v1.4.2")).toBeInTheDocument();
    expect(screen.getByText("feat: add pagination")).toBeInTheDocument();
    expect(screen.getByText("Passed")).toBeInTheDocument();
    expect(screen.getByText(/Not passed/)).toBeInTheDocument();
    expect(screen.getByText(/nothing is written to this repository/)).toBeInTheDocument();
  });

  it("labels a linked repository as read live and read-only", async () => {
    vi.spyOn(api, "repoPreview").mockResolvedValue(preview({ source: "github", repo: "octo/hello" }));
    render(<RepoSidebar selection={{ repo: "octo/hello", source: "github" }} />);
    expect(await screen.findByText("GitHub, read-only")).toBeInTheDocument();
    expect(screen.getByText(/Read live from public GitHub/)).toBeInTheDocument();
  });

  it("truncates a long tag list", async () => {
    const tags = Array.from({ length: 9 }, (_, i) => `v1.${i}.0`);
    vi.spyOn(api, "repoPreview").mockResolvedValue(preview({ tags }));
    render(<RepoSidebar selection={{ repo: "demo/acme-api", source: "demo" }} />);
    expect(await screen.findByText(/\+3 more/)).toBeInTheDocument();
  });

  it("explains when there is nothing to release", async () => {
    vi.spyOn(api, "repoPreview").mockResolvedValue(preview({ commits: [] }));
    render(<RepoSidebar selection={{ repo: "demo/acme-api", source: "demo" }} />);
    expect(await screen.findByText(/nothing to release/)).toBeInTheDocument();
  });

  it("explains a missing or private repository in plain words", async () => {
    vi.spyOn(api, "repoPreview").mockRejectedValue(new ApiError("repo_not_found", "x", 404));
    render(<RepoSidebar selection={{ repo: "ghost/none", source: "github" }} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Cannot read ghost/none");
    expect(alert).toHaveTextContent("Only public GitHub repositories");
  });

  it("explains a GitHub rate limit", async () => {
    vi.spyOn(api, "repoPreview").mockRejectedValue(new ApiError("github_rate_limited", "x", 429));
    render(<RepoSidebar selection={{ repo: "octo/hello", source: "github" }} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/rate limit/i);
  });
});

describe("NewRunForm", () => {
  function setup(opts: { link?: boolean; demo?: boolean } = {}) {
    vi.spyOn(api, "repos").mockResolvedValue({
      repos: ["demo/acme-api", "demo/breaking-change"],
      github_link: opts.link ?? true,
    });
    vi.spyOn(api, "health").mockResolvedValue(health({ demo_mode: opts.demo ?? true }));
    const onSelect = vi.fn();
    render(<NewRunForm onSelect={onSelect} />);
    return onSelect;
  }

  it("selects the first demo repository so the sidebar can show it", async () => {
    const onSelect = setup();
    await screen.findByLabelText("Repository");
    expect(onSelect).toHaveBeenCalledWith({ repo: "demo/acme-api", source: "demo" });
    fireEvent.change(screen.getByLabelText("Repository"), { target: { value: "demo/breaking-change" } });
    expect(onSelect).toHaveBeenLastCalledWith({ repo: "demo/breaking-change", source: "demo" });
  });

  it("disables linking when the server turned it off", async () => {
    setup({ link: false });
    const radio = await screen.findByLabelText(/Link a GitHub repository/);
    expect(radio).toBeDisabled();
    expect(screen.getByText("Turned off on this server.")).toBeInTheDocument();
  });

  it("validates owner/name, previews on request, and never asks for a token", async () => {
    const onSelect = setup();
    fireEvent.click(await screen.findByLabelText(/Link a GitHub repository/));
    const input = screen.getByLabelText("GitHub repository (owner/name)");
    fireEvent.change(input, { target: { value: "not a repo" } });
    expect(screen.getByText(/Use the form owner\/name/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview repository" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Start release run" })).toBeDisabled();
    expect(screen.queryByLabelText(/token/i)).toBeNull();

    fireEvent.change(input, { target: { value: "  psf/requests  " } });
    fireEvent.click(screen.getByRole("button", { name: "Preview repository" }));
    expect(onSelect).toHaveBeenLastCalledWith({ repo: "psf/requests", source: "github" });
    expect(screen.getByRole("button", { name: "Start release run" })).toBeEnabled();
  });

  it("rejects path tricks in the repository name", async () => {
    setup();
    fireEvent.click(await screen.findByLabelText(/Link a GitHub repository/));
    const input = screen.getByLabelText("GitHub repository (owner/name)");
    for (const bad of ["../etc/passwd", "a/b/c", "a/b?x=1", "-x/y", "a/.."]) {
      fireEvent.change(input, { target: { value: bad } });
      expect(screen.getByRole("button", { name: "Preview repository" })).toBeDisabled();
    }
  });

  it("starts a run for a linked repository with the trimmed name and no faults", async () => {
    setup();
    const create = vi.spyOn(api, "createRun").mockResolvedValue({
      id: "r1", repo: "psf/requests", source: "github", writes: "simulated", status: "created",
      error_code: null, created_at: "", updated_at: "",
    });
    fireEvent.click(await screen.findByLabelText(/Link a GitHub repository/));
    fireEvent.change(screen.getByLabelText("GitHub repository (owner/name)"), { target: { value: " psf/requests " } });
    fireEvent.click(screen.getByRole("button", { name: "Start release run" }));
    await waitFor(() => expect(create).toHaveBeenCalledWith("psf/requests", []));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/runs/r1"));
  });

  it("sends the chosen failures to the API", async () => {
    setup();
    const create = vi.spyOn(api, "createRun").mockResolvedValue({
      id: "r2", repo: "demo/acme-api", source: "demo", writes: "simulated", status: "created",
      error_code: null, created_at: "", updated_at: "",
    });
    fireEvent.click(await screen.findByLabelText(/Lose the reply to the publish call/));
    fireEvent.click(screen.getByRole("button", { name: "Start release run" }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith("demo/acme-api", [
        { step: "publish_release", kind: "timeout", count: 1 },
      ]),
    );
  });

  it("hides failure injection outside demo mode", async () => {
    setup({ demo: false });
    await screen.findByLabelText("Repository");
    expect(screen.queryByText("Inject failures (optional)")).toBeNull();
    expect(screen.getByText(/only available in demo mode/)).toBeInTheDocument();
  });

  it("shows the API error if starting fails and re-enables the button", async () => {
    setup();
    vi.spyOn(api, "createRun").mockRejectedValue(new ApiError("github_link_disabled", "Linking is off.", 403));
    fireEvent.click(await screen.findByRole("button", { name: "Start release run" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Linking is off.");
    expect(screen.getByRole("button", { name: "Start release run" })).toBeEnabled();
  });
});
