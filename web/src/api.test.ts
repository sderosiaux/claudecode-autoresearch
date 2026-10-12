import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  candidateValue,
  comparisonMetrics,
  controlPolicy,
  money,
  request,
} from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("supervisor transport", () => {
  it("preserves actionable server errors without pretending the mutation succeeded", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({
              detail: "Current protocol requires human approval",
            }),
            { status: 409, headers: { "content-type": "application/json" } },
          ),
        ),
    );
    await expect(
      request("/campaigns/c/run", { method: "POST", body: "{}" }),
    ).rejects.toMatchObject({
      status: 409,
      message: "Current protocol requires human approval",
    });
  });

  it("gives each mutation a durable idempotency key and does not attach one to reads", async () => {
    const fetch = vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(
          new Response("{}", {
            headers: { "content-type": "application/json" },
          }),
        ),
      );
    vi.stubGlobal("fetch", fetch);
    await request("/campaigns/c/run", { method: "POST", body: "{}" });
    await request("/campaigns/c/pause", { method: "POST", body: "{}" });
    await request("/campaigns/c");
    const keys = fetch.mock.calls.map(
      (call) => call[1].headers["Idempotency-Key"],
    );
    expect(keys[0]).toMatch(/^[\da-f-]{36}$/);
    expect(keys[1]).not.toBe(keys[0]);
    expect(keys[2]).toBeUndefined();
  });

  it("rejects the SPA fallback as invalid API data", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response("<html>workbench</html>", {
            headers: { "content-type": "text/html" },
          }),
        ),
    );
    await expect(request("/campaigns")).rejects.toBeInstanceOf(ApiError);
  });

  it("supports retained plain-text reports", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response("# Research report", {
            headers: { "content-type": "text/plain" },
          }),
        ),
    );
    await expect(request("/campaigns/c/report", {}, "text")).resolves.toBe(
      "# Research report",
    );
  });

  it("surfaces a disconnected supervisor without inventing an empty campaign list", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );
    await expect(request("/campaigns")).rejects.toMatchObject({
      status: 0,
      message: expect.stringContaining("Cannot reach the supervisor"),
    });
  });
});

describe("research state", () => {
  it("permits an approved awaiting-approval campaign to run and offers pause only for active work", () => {
    expect(controlPolicy("awaiting_approval", true).run).toBe(true);
    expect(controlPolicy("awaiting_approval", false).run).toBe(false);
    expect(controlPolicy("baselining", true).pause).toBe(true);
    expect(controlPolicy("paused", true)).toMatchObject({
      run: false,
      resume: true,
      pause: false,
    });
    expect(controlPolicy("blocked", true, true)).toMatchObject({
      run: false,
      resume: true,
    });
    expect(controlPolicy("blocked", true, false)).toMatchObject({
      run: true,
      resume: false,
    });
  });

  it("preserves micro-USD precision and does not treat absent values as zero", () => {
    expect(money(1)).toBe("$0.000001");
    expect(money(1234567)).toBe("$1.234567");
    expect(money(undefined)).toBe("—");
    expect(candidateValue(undefined)).toBeUndefined();
    expect(candidateValue({ candidate: 0 })).toBe(0);
    expect(candidateValue({ candidate: null })).toBeUndefined();
  });

  it("does not borrow confirmation metrics for skipped duplicate attempts", () => {
    expect(
      comparisonMetrics({
        termination_reason: "duplicate_program",
        confirmation: { metrics: { duration: { candidate: 2 } } },
      }),
    ).toEqual({});
  });

  it("uses independent confirmation metrics in preference to exploratory measurements", () => {
    const metrics = comparisonMetrics({
      comparison: { metrics: { duration: { candidate: 1 } } },
      confirmation: { metrics: { duration: { candidate: 2 } } },
    });
    expect(candidateValue(metrics.duration)).toBe(2);
  });
});
