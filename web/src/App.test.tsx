import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import type { CampaignDetail } from "./api";

const fixture: CampaignDetail = {
  campaign: {
    id: "test-campaign",
    name: "Parser allocations",
    goal: "Lower allocated bytes while preserving output.",
    repository: "/test/repository",
    state: "awaiting_approval",
    approved_hash: null,
  },
  protocol: {
    command: ["./benchmark.sh"],
    metrics: [
      { name: "allocated_bytes", unit: "bytes", direction: "minimize" },
    ],
  },
  protocol_hash: "hash-version-1",
  readiness: { status: "ready", findings: [] },
  budget: {
    limit: 1000000,
    consumed: 1,
    engaged: 0,
    protected: 200000,
    available: 799999,
    enforcement: "strict",
    scope: "Manual research: no model calls.",
  },
  experiments: [],
  events: [],
  comparison: [],
};

beforeEach(() => window.history.replaceState(null, "", "/"));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mockSupervisor(detail = structuredClone(fixture)) {
  const calls: { path: string; options: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, options: RequestInit = {}) => {
      calls.push({ path, options });
      let body: unknown = {};
      if (path === "/api/campaigns")
        body =
          options.method === "POST"
            ? { campaign: detail.campaign }
            : [detail.campaign];
      else if (path === "/api/capabilities")
        body = {
          provider: {
            strict_budget: false,
            reason: "Strict billing cannot be verified.",
          },
          claude_code: {
            strict_budget: false,
            reason: "Strict billing cannot be verified.",
          },
        };
      else if (path === "/api/campaigns/test-campaign") body = detail;
      else if (path.endsWith("/approve")) {
        detail.campaign.approved_hash = detail.protocol_hash;
        body = detail;
      } else if (path.endsWith("/reconcile")) {
        Object.assign(detail.campaign, {
          recovery_required: false,
          state: "blocked",
          reconciliation: { next_action: "resume explicitly" },
        });
        body = detail;
      } else if (path.endsWith("/protocol") && options.method === "PUT") {
        detail.protocol = JSON.parse(String(options.body)).protocol;
        detail.protocol_hash = "new-hash";
        detail.campaign.approved_hash = null;
        detail.readiness = null;
        body = detail;
      }
      return new Response(JSON.stringify(body), {
        headers: { "content-type": "application/json" },
      });
    }),
  );
  return calls;
}

function diagnosticFixture() {
  const detail = structuredClone(fixture);
  detail.campaign.approved_hash = detail.protocol_hash;
  detail.protocol!.diagnostic_command = ["./profile.sh"];
  return detail;
}

it("runs diagnostics only on an explicit click and refreshes retained results", async () => {
  const user = userEvent.setup();
  const detail = diagnosticFixture();
  const calls = mockSupervisor(detail);
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Preparation" }));
  const button = await screen.findByRole("button", { name: "Run diagnostic" });
  expect(button).toBeEnabled();
  await user.click(screen.getByText("Diagnostic command"));
  await user.click(screen.getByRole("button", { name: /Refresh/ }));
  expect(calls.filter((call) => call.options.method === "POST")).toHaveLength(0);
  detail.campaign.diagnostics = [{ label: "profile-new", status: "completed", stdout: "profile captured", artifact: "artifacts/profile-new", included_in_comparison: false }];
  await user.click(button);
  await screen.findByText("profile-new");
  const posted = calls.filter((call) => call.path.endsWith("/profile"));
  expect(posted).toHaveLength(1);
  expect(posted[0].options.method).toBe("POST");
  expect(posted[0].options.headers).toHaveProperty("Idempotency-Key");
  expect(screen.getByText("Excluded from comparison")).toBeVisible();
  expect(screen.queryByText("confirmed")).not.toBeInTheDocument();
});

it.each([
  "missing command", "changed protocol", "active campaign", "active scheduler",
  "recovery", "budget incident", "budget exhausted", "duration exhausted",
])("does not admit a diagnostic with %s", async (condition) => {
  const user = userEvent.setup();
  const detail = diagnosticFixture();
  if (condition === "missing command") delete detail.protocol!.diagnostic_command;
  if (condition === "changed protocol") detail.protocol_hash = "changed-hash";
  if (condition === "active campaign") detail.campaign.state = "exploring";
  if (condition === "active scheduler") detail.campaign.scheduler = { active_tasks: 1 };
  if (condition === "recovery") detail.campaign.recovery_required = true;
  if (condition === "budget incident") detail.campaign.budget_incident = true;
  if (condition === "budget exhausted") detail.campaign.termination_reason = "budget_exhausted";
  if (condition === "duration exhausted") Object.assign(detail.campaign, { limits: { max_active_seconds: 60 }, active_seconds: 60 });
  const calls = mockSupervisor(detail);
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Preparation" }));
  const button = await screen.findByRole("button", { name: "Run diagnostic" });
  expect(button).toBeDisabled();
  await user.click(button);
  expect(calls.some((call) => call.path.endsWith("/profile"))).toBe(false);
});

it("retains failed diagnostics, bounds previews, and keeps cleanup warnings out of performance results", async () => {
  const user = userEvent.setup();
  const detail = diagnosticFixture();
  detail.campaign.diagnostics = [
    { label: "profile-failed", status: "failed", stdout: "x".repeat(8000) + "HIDDEN_STDOUT", stderr: "e".repeat(2000) + "HIDDEN_STDERR", artifact: "artifacts/profile-failed" },
    { label: "profile-warning", status: "completed", cleanup_warning: "Network cleanup could not be verified", artifact: "artifacts/../outside" },
  ];
  const calls = mockSupervisor(detail);
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Preparation" }));
  await user.click(screen.getByText("profile-failed"));
  const failed = screen.getByText("profile-failed").closest("details")!;
  expect(within(failed).getByText("failed")).toBeVisible();
  await user.click(within(failed).getByText("stdout (up to 8,000 characters)"));
  await user.click(within(failed).getByText("stderr (up to 2,000 characters)"));
  expect(within(failed).getByText("x".repeat(8000))).toBeVisible();
  expect(screen.queryByText(/HIDDEN_STD/)).not.toBeInTheDocument();
  expect(within(failed).getByRole("link", { name: /execution.json/ })).toHaveAttribute("href", "/api/campaigns/test-campaign/artifacts/profile-failed/execution.json");
  await user.click(screen.getByText("profile-warning"));
  const warning = screen.getByText("profile-warning").closest("details")!;
  expect(within(warning).getByText("Cleanup warning")).toHaveClass("warning");
  expect(within(warning).getByRole("status")).toHaveTextContent("Network cleanup could not be verified");
  expect(within(warning).queryByRole("link")).not.toBeInTheDocument();
  expect(screen.getAllByText("Excluded from comparison")).toHaveLength(2);
  await user.click(screen.getByRole("button", { name: /Refresh/ }));
  expect(screen.getByText("profile-failed")).toBeVisible();
  expect(calls.some((call) => call.path.endsWith("/profile"))).toBe(false);
});

it("shows diagnostic admission errors without losing earlier evidence or retrying", async () => {
  const user = userEvent.setup();
  const detail = diagnosticFixture();
  detail.campaign.diagnostics = [{ label: "profile-earlier", status: "failed" }];
  mockSupervisor(detail);
  const original = globalThis.fetch;
  const rejected = vi.fn(() => Promise.resolve(new Response(JSON.stringify({ detail: "Another campaign requires resource cleanup." }), { status: 409, headers: { "content-type": "application/json" } })));
  vi.stubGlobal("fetch", vi.fn((path: string, options?: RequestInit) => path.endsWith("/profile") ? rejected() : original(path, options)));
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Preparation" }));
  await user.click(await screen.findByRole("button", { name: "Run diagnostic" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Another campaign requires resource cleanup.");
  expect(screen.getByText("profile-earlier")).toBeVisible();
  expect(rejected).toHaveBeenCalledTimes(1);
});

it("approves only the exact reviewed protocol hash, then enables the real run action", async () => {
  const user = userEvent.setup();
  const calls = mockSupervisor();
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Preparation" }));
  const approval = await screen.findByRole("button", {
    name: /Approve this protocol/,
  });
  expect(approval).toBeDisabled();
  expect(screen.getByRole("button", { name: /Run campaign/ })).toBeDisabled();
  await user.click(
    screen.getByRole("checkbox", { name: /I reviewed the workload/ }),
  );
  expect(approval).toBeEnabled();
  await user.click(approval);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /Run campaign/ })).toBeEnabled(),
  );
  const call = calls.find((call) => call.path.endsWith("/approve"));
  expect(JSON.parse(String(call?.options.body))).toEqual({
    protocol_hash: "hash-version-1",
  });
  expect(screen.getByText("$0.000001")).toBeVisible();
});

it.each(["claude", "claude_code"])(
  "blocks unverified strict %s mode and requires an explicit estimated selection",
  async (agent) => {
    const user = userEvent.setup();
    mockSupervisor();
    render(<App />);
    await screen.findByRole("heading", { name: "Parser allocations" });
    await user.click(screen.getByRole("button", { name: /New campaign/ }));
    await user.selectOptions(
      screen.getByLabelText("Research proposals"),
      agent,
    );
    expect(
      screen.getByRole("button", { name: /Create campaign/ }),
    ).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Strict billing cannot be verified",
    );
    await user.selectOptions(
      screen.getByLabelText("Model spending enforcement"),
      "estimated",
    );
    expect(
      screen.getByRole("button", { name: /Create campaign/ }),
    ).toBeEnabled();
    expect(
      screen.getByRole("checkbox", { name: /not a guaranteed maximum/ }),
    ).not.toBeChecked();
  },
);

it("invalidates the review checkbox when the protocol hash changes", async () => {
  const user = userEvent.setup();
  const detail = structuredClone(fixture);
  mockSupervisor(detail);
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Preparation" }));
  const checkbox = await screen.findByRole("checkbox", {
    name: /I reviewed the workload/,
  });
  await user.click(checkbox);
  expect(
    screen.getByRole("button", { name: /Approve this protocol/ }),
  ).toBeEnabled();
  detail.protocol_hash = "hash-version-2";
  await user.click(screen.getByRole("button", { name: /Refresh/ }));
  await waitFor(() => expect(checkbox).not.toBeChecked());
  expect(
    screen.getByRole("button", { name: /Approve this protocol/ }),
  ).toBeDisabled();
});

it("keeps a failed readiness request visible and never changes the campaign into a running state", async () => {
  const user = userEvent.setup();
  mockSupervisor();
  const original = globalThis.fetch;
  vi.stubGlobal(
    "fetch",
    vi.fn((path: string, options?: RequestInit) =>
      path.endsWith("/assess")
        ? Promise.resolve(
            new Response(
              JSON.stringify({
                detail: "Runner unavailable: Docker is not installed.",
              }),
              { status: 409, headers: { "content-type": "application/json" } },
            ),
          )
        : original(path, options),
    ),
  );
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Preparation" }));
  await user.click(
    await screen.findByRole("button", { name: /Reassess readiness/ }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Runner unavailable: Docker is not installed.",
  );
  expect(screen.getByRole("button", { name: /Run campaign/ })).toBeDisabled();
});

it("requires explicit reconciliation after a crash and never starts work automatically", async () => {
  const user = userEvent.setup();
  const detail = structuredClone(fixture);
  Object.assign(detail.campaign, {
    state: "blocked",
    recovery_required: true,
    approved_hash: detail.protocol_hash,
    runner: { kind: "docker" },
    cleanup_warning: "Container cleanup could not be confirmed",
    resource_lease_expires_at: "2026-10-12T04:56:26Z",
  });
  const calls = mockSupervisor(detail);
  render(<App />);
  const reconcile = await screen.findByRole("button", {
    name: "Reconcile recovery",
  });
  expect(screen.getByRole("button", { name: "Run campaign" })).toBeDisabled();
  expect(calls.some((call) => call.path.endsWith("/reconcile"))).toBe(false);
  expect(screen.getByText("2026-10-12T04:56:26Z")).toBeInTheDocument();
  expect(screen.getByText("Container cleanup could not be confirmed")).toBeInTheDocument();
  expect(screen.queryByText(/Trusted host recovery stays blocked/)).not.toBeInTheDocument();
  await user.click(reconcile);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Resume" })).toBeEnabled(),
  );
  expect(
    screen.queryByRole("region", { name: "Coordinator recovery required" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent(
    "Existing reservations remain counted",
  );
  expect(calls.filter((call) => call.path.endsWith("/reconcile"))).toHaveLength(
    1,
  );
  expect(calls.some((call) => /\/(run|resume)$/.test(call.path))).toBe(false);
});

it("preserves a watchdog refusal and keeps recovery unresolved", async () => {
  const user = userEvent.setup();
  const detail = structuredClone(fixture);
  Object.assign(detail.campaign, {
    state: "paused",
    recovery_required: true,
    approved_hash: detail.protocol_hash,
  });
  mockSupervisor(detail);
  const original = globalThis.fetch;
  vi.stubGlobal(
    "fetch",
    vi.fn((path: string, options?: RequestInit) =>
      path.endsWith("/reconcile")
        ? Promise.resolve(
            new Response(
              JSON.stringify({
                detail:
                  "Watchdog lease has not expired; recovery remains blocked.",
              }),
              { status: 409, headers: { "content-type": "application/json" } },
            ),
          )
        : original(path, options),
    ),
  );
  render(<App />);
  await user.click(
    await screen.findByRole("button", { name: "Reconcile recovery" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Watchdog lease has not expired",
  );
  expect(screen.getByRole("button", { name: "Resume" })).toBeDisabled();
  expect(
    screen.getByRole("region", { name: "Coordinator recovery required" }),
  ).toBeVisible();
});

it("filters real campaign names and goals and restores collapsed navigation", async () => {
  const user = userEvent.setup();
  mockSupervisor();
  render(<App />);
  await screen.findByRole("heading", { name: "Parser allocations" });
  await user.type(
    screen.getByRole("searchbox", { name: "Search campaigns" }),
    "preserving output",
  );
  expect(
    screen.getByRole("button", { name: /Parser allocations/ }),
  ).toBeVisible();
  await user.clear(screen.getByRole("searchbox", { name: "Search campaigns" }));
  await user.type(
    screen.getByRole("searchbox", { name: "Search campaigns" }),
    "does not exist",
  );
  expect(screen.getByRole("status")).toHaveTextContent("No matching campaigns");
  await user.click(screen.getByRole("button", { name: "Clear search" }));
  expect(
    screen.getByRole("button", { name: /Parser allocations/ }),
  ).toBeVisible();
  await user.click(
    screen.getByRole("button", { name: "Collapse campaign navigation" }),
  );
  expect(
    screen.queryByRole("complementary", { name: "Campaign navigation" }),
  ).not.toBeInTheDocument();
  await user.click(
    screen.getByRole("button", { name: "Expand campaign navigation" }),
  );
  expect(
    screen.getByRole("complementary", { name: "Campaign navigation" }),
  ).toBeVisible();
});

it("closes temporary navigation with Escape and returns focus to its trigger", async () => {
  vi.stubGlobal("innerWidth", 390);
  const user = userEvent.setup();
  mockSupervisor();
  render(<App />);
  const trigger = await screen.findByRole("button", {
    name: "Expand campaign navigation",
  });
  await user.click(trigger);
  expect(
    screen.getByRole("complementary", { name: "Campaign navigation" }),
  ).toBeVisible();
  await user.keyboard("{Escape}");
  expect(
    screen.queryByRole("complementary", { name: "Campaign navigation" }),
  ).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

it("creates a guided benchmark draft without approving or starting work", async () => {
  const user = userEvent.setup();
  const calls = mockSupervisor();
  render(<App />);
  await screen.findByRole("heading", { name: "Parser allocations" });
  await user.click(screen.getByRole("button", { name: /New campaign/ }));
  await user.type(
    screen.getByLabelText("Research goal"),
    "Reduce parser allocation",
  );
  await user.type(screen.getByLabelText("Repository path"), "/test/repository");
  await user.type(screen.getByLabelText("Campaign budget"), "2.50");
  await user.selectOptions(
    screen.getByLabelText("Measurement setup"),
    "guided",
  );
  await user.type(
    screen.getByLabelText("Benchmark command"),
    "sh benchmark.sh",
  );
  await user.type(screen.getByLabelText("Objective metric"), "allocated_bytes");
  await user.type(screen.getByLabelText("Unit"), "bytes");
  await user.type(
    screen.getByLabelText("Protected evaluator paths"),
    "benchmark.sh\nchecks/",
  );
  await user.click(screen.getByRole("button", { name: /Create campaign/ }));
  await waitFor(() =>
    expect(
      calls.some(
        (call) =>
          call.path === "/api/campaigns" && call.options.method === "POST",
      ),
    ).toBe(true),
  );
  const body = JSON.parse(
    String(
      calls.find(
        (call) =>
          call.path === "/api/campaigns" && call.options.method === "POST",
      )?.options.body,
    ),
  );
  expect(body).toMatchObject({
    name: "Reduce parser allocation",
    budget_usd: "2.5",
    limits: { max_parallel_tasks: 2 },
    repository: "/test/repository",
    agent: { kind: "manual", enforcement: "strict" },
    runner: { kind: "docker" },
    protocol: {
      command: ["sh", "-c", "sh benchmark.sh"],
      repetitions: 3,
      confirmation_repetitions: 3,
      timeout_seconds: 60,
      protected_paths: ["benchmark.sh", "checks/"],
      metrics: [
        {
          name: "allocated_bytes",
          unit: "bytes",
          role: "objective",
          direction: "minimize",
          minimum_improvement: 0.02,
        },
      ],
    },
  });
  expect(calls.some((call) => /\/(run|approve|resume)$/.test(call.path))).toBe(
    false,
  );
});

it("saves a missing protocol through guided preparation and requires reassessment before approval", async () => {
  const user = userEvent.setup();
  const detail = structuredClone(fixture);
  detail.protocol = null;
  detail.protocol_hash = null;
  const calls = mockSupervisor(detail);
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Preparation" }));
  await user.click(
    await screen.findByRole("button", {
      name: "Configure measurement protocol",
    }),
  );
  await user.type(screen.getByLabelText("Benchmark command"), "./benchmark.sh");
  await user.type(screen.getByLabelText("Objective metric"), "duration");
  await user.type(screen.getByLabelText("Unit"), "ms");
  await user.click(screen.getByRole("button", { name: "Save protocol" }));
  const approval = await screen.findByRole("button", {
    name: "Approve this protocol",
  });
  expect(approval).toBeDisabled();
  await user.click(
    screen.getByRole("checkbox", { name: /I reviewed the workload/ }),
  );
  expect(approval).toBeDisabled();
  expect(screen.getByRole("button", { name: /Run campaign/ })).toBeDisabled();
  expect(
    JSON.parse(
      String(calls.find((call) => call.options.method === "PUT")?.options.body),
    ).protocol.command,
  ).toEqual(["sh", "-c", "./benchmark.sh"]);
  expect(calls.some((call) => call.path.endsWith("/approve"))).toBe(false);
});

it("keeps a complete expert protocol draft across refreshes", async () => {
  const user = userEvent.setup();
  const detail = structuredClone(fixture);
  detail.protocol!.environment = { JVM: "test" };
  mockSupervisor(detail);
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Preparation" }));
  await user.click(
    await screen.findByRole("button", { name: "Edit protocol manifest" }),
  );
  const editor = screen.getByLabelText("Protocol manifest (JSON)");
  expect(editor).toHaveValue(JSON.stringify(detail.protocol, null, 2));
  await user.clear(editor);
  await user.click(editor);
  await user.paste('{ "unsaved": true }');
  await user.click(screen.getByRole("button", { name: /Refresh/ }));
  expect(editor).toHaveValue('{ "unsaved": true }');
  expect(screen.queryByLabelText("Benchmark command")).not.toBeInTheDocument();
});

it("retains duplicate history without presenting inherited measurements as its own evidence", async () => {
  const user = userEvent.setup();
  const detail = structuredClone(fixture);
  detail.experiments = [
    {
      id: "source",
      hypothesis: "Source patch",
      protocol_hash: detail.protocol_hash,
      verdict: "confirmed",
    },
    {
      id: "duplicate",
      hypothesis: "Same resulting program",
      protocol_hash: detail.protocol_hash,
      status: "cancelled",
      verdict: "inconclusive",
      termination_reason: "duplicate_program",
      duplicate_of: "source",
      duplicate_reason: "No new measurements were performed.",
      metrics: { stale_value: 42 },
      confirmation: { metrics: { stale_value: { candidate: 42 } } },
      confirmation_runs: [{ metrics: { stale_value: 42 } }],
    },
  ];
  mockSupervisor(detail);
  render(<App />);
  await screen.findByRole("heading", { name: "Parser allocations" });
  await user.click(screen.getByRole("button", { name: /Experiments 2/ }));
  expect(
    screen.getByText("Duplicate program · skipped", {
      selector: "span.status",
    }),
  ).toBeVisible();
  await user.click(
    screen.getByRole("button", {
      name: "Review evidence for Same resulting program",
    }),
  );
  expect(screen.getByText("No new measurements were performed.")).toBeVisible();
  expect(
    screen.queryByRole("heading", { name: "Measured values" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText("Candidate confirmation runs"),
  ).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "View source attempt" }));
  expect(screen.getByRole("heading", { name: "Source patch" })).toBeVisible();
});

it("excludes historical protocol measurements from current comparison labels", async () => {
  const user = userEvent.setup();
  const detail = structuredClone(fixture);
  detail.protocol = {
    metrics: [{ name: "duration", unit: "s", role: "objective" }],
  };
  detail.campaign.baseline_summary = {
    protocol_hash: "old-hash",
    metrics: { duration: { mean: 1000, unit: "ms" } },
    valid: true,
  };
  detail.experiments = [
    {
      id: "old",
      hypothesis: "Historical milliseconds",
      protocol_hash: "old-hash",
      verdict: "confirmed",
      confirmation: { metrics: { duration: { candidate: 800 } } },
    },
  ];
  detail.comparison = detail.experiments;
  mockSupervisor(detail);
  render(<App />);
  await screen.findByRole("heading", { name: "Parser allocations" });
  await user.click(screen.getByRole("button", { name: "Comparison" }));
  expect(
    screen.getByText(/1 prior-protocol attempt is retained/),
  ).toBeVisible();
  expect(
    screen.queryByRole("heading", { name: "Reference baseline" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("columnheader", { name: /duration/ }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "No candidate measurements yet" }),
  ).toBeVisible();
});

it.each([
  ["measuring", "Comparison in progress"],
  ["confirming", "Confirmation in progress"],
])("shows current %s work before an aggregate comparison exists", async (phase, title) => {
  const user = userEvent.setup();
  const detail = structuredClone(fixture);
  detail.experiments = [{
    id: "candidate-in-progress",
    hypothesis: "Reduce retained allocation",
    protocol_hash: detail.protocol_hash,
    status: "running",
    phase,
  }];
  detail.events = [{
    seq: 1,
    type: "execution.finished",
    created_at: "2026-10-12T05:33:00Z",
    data: { label: "candidate-0", status: "succeeded" },
  }];
  const calls = mockSupervisor(detail);
  render(<App />);
  await user.click(await screen.findByRole("button", { name: "Comparison" }));
  expect(screen.getByRole("heading", { name: title })).toBeVisible();
  expect(screen.getByText(/Review individual executions in Activity/)).toBeVisible();
  expect(screen.getByText(/after the required repetitions finish/)).toBeVisible();
  expect(
    screen.queryByRole("heading", { name: "No candidate measurements yet" }),
  ).not.toBeInTheDocument();
  expect(calls.some((call) => call.options.method === "POST")).toBe(false);
});

it("shows derived legacy context with its recorded provenance limit", async () => {
  const user = userEvent.setup();
  const detail = structuredClone(fixture);
  const warning =
    "Protocol and revisions verified from recorded runs; requested runner identity was not recorded.";
  detail.protocol = {
    metrics: [{ name: "duration", unit: "ms", role: "objective" }],
  };
  detail.campaign.baseline_summary = {
    protocol_hash: detail.protocol_hash,
    metrics: { duration: { mean: 1000, unit: "ms" } },
    valid: true,
    measurement_context_source: "legacy_recorded_runs",
    measurement_context_warning: warning,
  };
  const candidate = {
    id: "legacy",
    hypothesis: "Measured legacy candidate",
    protocol_hash: detail.protocol_hash,
    verdict: "confirmed",
    measurement_context_source: "legacy_recorded_runs",
    measurement_context_warning: warning,
    confirmation: { metrics: { duration: { baseline: 1000, candidate: 800 } } },
  };
  detail.experiments = [candidate];
  detail.comparison = [candidate];
  mockSupervisor(detail);
  render(<App />);
  await screen.findByRole("heading", { name: "Parser allocations" });
  await user.click(screen.getByRole("button", { name: "Comparison" }));
  expect(
    screen.getByRole("heading", { name: "Reference baseline" }),
  ).toBeVisible();
  expect(screen.getByText("Measured legacy candidate")).toBeVisible();
  expect(
    screen.getByRole("columnheader", { name: /duration ms/ }),
  ).toBeVisible();
  expect(screen.getAllByText(warning)).toHaveLength(2);
});

it("defaults to Overview and opens the selected attempt evidence without starting work", async () => {
  const user = userEvent.setup();
  const detail = structuredClone(fixture);
  detail.experiments = [
    {
      id: "evidence-attempt",
      hypothesis: "Remove allocation copy",
      protocol_hash: detail.protocol_hash,
      status: "succeeded",
      verdict: "confirmed",
      expected_effect: "Lower allocation",
      observations: "Resolved independent confirmation.",
    },
  ];
  detail.insights = {
    available: true,
    summary: {
      attempts: 1,
      current: 1,
      confirmed: 1,
      historical: 0,
      measured_runs: 12,
    },
    metrics: [],
    tracks: [
      {
        id: "allocation",
        title: "Allocation reduction",
        experiments: [
          {
            ...detail.experiments[0],
            category: "confirmed",
            current_context: true,
            reason: "Resolved independent confirmation.",
          },
        ],
      },
    ],
  };
  const calls = mockSupervisor(detail);
  render(<App />);
  expect(
    await screen.findByRole("heading", { name: "Campaign overview" }),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "Overview" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await user.click(
    screen.getByRole("button", { name: "Remove allocation copy" }),
  );
  expect(
    await screen.findByRole("region", { name: "Experiment evidence" }),
  ).toHaveTextContent("Resolved independent confirmation.");
  expect(screen.getByRole("button", { name: /Experiments 1/ })).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect(calls.some((call) => /\/(run|resume|approve)$/.test(call.path))).toBe(
    false,
  );
  await user.click(screen.getByRole("button", { name: "Overview" }));
  await user.click(screen.getByRole("button", { name: "Add hypothesis" }));
  expect(
    screen.getByRole("heading", { name: "Propose a manual experiment" }),
  ).toBeVisible();
});
