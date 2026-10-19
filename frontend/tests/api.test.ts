import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, describeError } from "@/lib/api";

const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

afterEach(() => vi.restoreAllMocks());

describe("api client", () => {
  it("sends JSON to the right path and returns the body", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok({ id: "r1" }));
    const run = await api.createRun("demo/acme-api", [{ step: "run_checks", kind: "timeout", count: 1 }]);
    expect(run).toEqual({ id: "r1" });
    const [url, init] = spy.mock.calls[0]!;
    expect(String(url)).toMatch(/\/v1\/runs$/);
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      repo: "demo/acme-api",
      faults: [{ step: "run_checks", kind: "timeout", count: 1 }],
    });
  });

  it("encodes the run id in the path", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(ok({}));
    await api.getRun("a/b?c");
    expect(String(spy.mock.calls[0]![0])).toMatch(/\/v1\/runs\/a%2Fb%3Fc$/);
  });

  it("turns the error envelope into an ApiError", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "run_not_found", message: "No such run." } }), { status: 404 }),
    );
    await expect(api.getRun("x")).rejects.toMatchObject({ code: "run_not_found", status: 404 });
  });

  it("reads Retry-After on a rate limit", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ error: { code: "rate_limited", message: "Too many." } }), {
        status: 429,
        headers: { "Retry-After": "12" },
      }),
    );
    const err = await api.listRuns().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.retryAfter).toBe(12);
    expect(describeError(err)).toBe("Too many requests. Try again in 12 seconds.");
  });

  it("survives a non-JSON error body", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("<html>bad gateway</html>", { status: 502 }));
    await expect(api.health()).rejects.toMatchObject({ code: "http_error", status: 502 });
  });

  it("reports an unreachable API with advice", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("fetch failed"));
    const err = await api.health().catch((e) => e);
    expect(err.code).toBe("unreachable");
    expect(describeError(err)).toMatch(/NEXT_PUBLIC_API_URL/);
  });

  it("reports a timeout", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new DOMException("aborted", "AbortError"));
    const err = await api.health().catch((e) => e);
    expect(err.code).toBe("timeout");
  });

  it("explains an empty repository in plain words", () => {
    expect(describeError(new ApiError("repo_empty", "x", 409))).toMatch(/no commits yet.*Push your code/);
  });

  it("describes unknown errors generically", () => {
    expect(describeError(new Error("boom"))).toBe("Something went wrong.");
  });
});
