import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Overview from "./Overview";
import type { CampaignDetail } from "./api";

afterEach(cleanup);
function example(): CampaignDetail {
  return {
    campaign: {
      id: "campaign",
      name: "Parser",
      goal: "Lower duration with zero errors",
      repository: "/repo",
      state: "reviewable",
      approved_hash: "current",
      scheduler: {
        phase: "preparing",
        max_parallel_tasks: 3,
        active_tasks: 2,
        waiting_for_measurement: 2,
        measurement_concurrency: 1,
        tasks: [
          {
            id: "p1",
            kind: "candidate_preparation",
            status: "running",
            experiment_id: "good",
          },
          {
            id: "p2",
            kind: "candidate_preparation",
            status: "running",
            experiment_id: "bad",
          },
          {
            id: "m1",
            kind: "measurement",
            status: "queued",
            experiment_id: "pending",
          },
        ],
      },
    },
    protocol: {
      metrics: [
        { name: "duration", unit: "ms", role: "objective" },
        {
          name: "errors",
          unit: "errors",
          role: "constraint",
          operator: "lte",
          value: 0,
        },
      ],
    },
    protocol_hash: "current",
    readiness: { status: "ready" },
    budget: {
      limit: 1000000,
      consumed: 0,
      protected: 100000,
      engaged: 0,
      available: 900000,
    },
    experiments: [],
    events: [],
    comparison: [],
    insights: {
      available: true,
      summary: {
        attempts: 4,
        current: 3,
        confirmed: 1,
        inconclusive: 0,
        constraint_failed: 1,
        failed: 0,
        skipped: 1,
        historical: 1,
        measured_runs: 12,
      },
      metrics: [
        {
          name: "duration",
          unit: "ms",
          direction: "minimize",
          role: "objective",
          baseline: 10,
          baseline_context_warning: "Runner identity was not recorded.",
          best: {
            experiment_id: "good",
            hypothesis: "Remove a copy",
            baseline: 10,
            candidate: 8,
            improvement_fraction: 0.2,
            gain_ci95: [1, 3],
            evidence: "confirmed",
            measurement_context_warning: "Runner identity was not recorded.",
          },
          points: [
            {
              experiment_id: "good",
              sequence: 1,
              hypothesis: "Remove a copy",
              baseline: 10,
              candidate: 8,
              improvement_fraction: 0.2,
              gain_ci95: [1, 3],
              evidence: "confirmed",
            },
          ],
        },
        {
          name: "errors",
          unit: "errors",
          direction: "minimize",
          role: "constraint",
          operator: "lte",
          threshold: 0,
          baseline: 0,
          best: null,
          points: [
            {
              experiment_id: "good",
              hypothesis: "Remove a copy",
              baseline: 0,
              candidate: 0,
              improvement_fraction: null,
              gain_ci95: [0, 0],
              evidence: "confirmed",
              verdict: "confirmed",
            },
          ],
        },
      ],
      tracks: [
        {
          id: "allocation",
          title: "Reduce allocation",
          falsifier: "No resolved gain",
          tradeoffs: ["More code"],
          experiments: [
            {
              id: "good",
              sequence: 1,
              hypothesis: "Remove a copy",
              expected_effect: "Lower duration",
              category: "confirmed",
              status: "succeeded",
              verdict: "confirmed",
              current_context: true,
              phase: "finished",
              reason: "Independent confirmation resolved a gain.",
            },
            {
              id: "bad",
              sequence: 2,
              hypothesis: "Skip validation",
              expected_effect: "Lower duration",
              category: "constraint_failed",
              status: "succeeded",
              verdict: "constraint_failed",
              current_context: true,
              reason: "Error count exceeded zero; stop this branch.",
            },
          ],
        },
        {
          id: "history",
          title: "Earlier protocol",
          experiments: [
            {
              id: "old",
              sequence: 3,
              hypothesis: "Historical candidate",
              category: "historical",
              status: "succeeded",
              verdict: "confirmed",
              current_context: false,
              reason: "Earlier protocol evidence.",
            },
          ],
        },
        {
          id: "skip",
          title: "Duplicate branch",
          experiments: [
            {
              id: "duplicate",
              sequence: 4,
              hypothesis: "Same program",
              category: "skipped",
              status: "cancelled",
              verdict: "inconclusive",
              current_context: true,
              reason: "Identical program; no new measurements.",
            },
          ],
        },
      ],
    },
  };
}
function setup(detail = example()) {
  const handlers = {
    onPreparation: vi.fn(),
    onComparison: vi.fn(),
    onExperiment: vi.fn(),
    onAddHypothesis: vi.fn(),
  };
  render(<Overview detail={detail} {...handlers} />);
  return handlers;
}

it("distinguishes an active campaign awaiting admission from an idle scheduler", () => {
  const detail = example();
  detail.campaign.active_started_at = "2026-10-12T05:44:30Z";
  detail.campaign.scheduler = {
    phase: "finished",
    active_tasks: 0,
    max_parallel_tasks: 1,
    measurement_concurrency: 1,
    waiting_for_measurement: 0,
    tasks: [],
  };
  const handlers = {
    onExperiment: vi.fn(),
    onPreparation: vi.fn(),
    onComparison: vi.fn(),
    onAddHypothesis: vi.fn(),
  };
  const view = render(<Overview detail={detail} {...handlers} />);
  const scheduler = screen.getByRole("region", { name: "Research scheduling" });
  expect(
    within(scheduler).getByText(
      "Campaign is active and waiting for its next task to be admitted.",
    ),
  ).toBeVisible();
  expect(
    within(scheduler).queryByText(/No active or queued tasks reported/),
  ).not.toBeInTheDocument();
  detail.campaign.active_started_at = null;
  view.rerender(<Overview detail={detail} {...handlers} />);
  expect(
    within(scheduler).getByText(/No active or queued tasks reported/),
  ).toBeVisible();
  expect(
    within(scheduler).queryByText(/Campaign is active and waiting/),
  ).not.toBeInTheDocument();
});

it("renders absolute gain intervals with units and preserves a zero-valued hard constraint", () => {
  setup();
  expect(
    screen.getByText("95% gain interval: 1 to 3 ms", {
      selector: ".metric-gain",
    }),
  ).toBeVisible();
  expect(screen.getByText("+20%")).toBeVisible();
  expect(screen.queryByText(/100% to.*300%/)).not.toBeInTheDocument();
  const constraint = screen
    .getByText("errors", { selector: "strong" })
    .closest("tr")!;
  expect(within(constraint).getByText("≤ 0 errors")).toBeVisible();
  expect(within(constraint).getAllByText("0")).toHaveLength(2);
  expect(
    within(constraint).queryByText("No confirmed gain"),
  ).not.toBeInTheDocument();
  expect(screen.getByText("Runner identity was not recorded.")).toBeVisible();
});

it("filters failed and historical attempts without treating old confirmations as current wins", async () => {
  const user = userEvent.setup();
  setup();
  await user.click(
    screen.getByRole("button", { name: "Failed / constraints 1" }),
  );
  expect(screen.getByRole("button", { name: "Skip validation" })).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Historical candidate" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByText("Error count exceeded zero; stop this branch."),
  ).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Confirmed 1" }));
  expect(
    screen.getByText("Remove a copy", { selector: "button.hypothesis-link" }),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Historical candidate" }),
  ).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Prior context 1" }));
  expect(
    screen.getByRole("button", { name: "Historical candidate" }),
  ).toBeVisible();
  expect(screen.getByText("Prior measurement context")).toBeVisible();
});

it("opens evidence for the exact selected attempt and exposes the real scheduler queue", async () => {
  const user = userEvent.setup();
  const handlers = setup();
  const scheduler = screen.getByRole("region", { name: "Research scheduling" });
  expect(within(scheduler).getByText("Up to 3")).toBeVisible();
  expect(within(scheduler).getByText("1 · serialized")).toBeVisible();
  expect(
    within(scheduler).getByText("Waiting for measurement").nextElementSibling,
  ).toHaveTextContent("2");
  await user.click(screen.getByRole("button", { name: "Skip validation" }));
  expect(handlers.onExperiment).toHaveBeenCalledWith("bad");
  await user.click(screen.getByRole("button", { name: "Add hypothesis" }));
  expect(handlers.onAddHypothesis).toHaveBeenCalledOnce();
});

it("shows missing projections explicitly and routes setup without inventing KPI values", async () => {
  const user = userEvent.setup();
  const detail = example();
  delete detail.insights;
  delete detail.campaign.scheduler;
  detail.protocol = null;
  detail.protocol_hash = null;
  const handlers = setup(detail);
  expect(
    screen.getByRole("heading", { name: "Overview summary unavailable" }),
  ).toBeVisible();
  expect(screen.queryByRole("definition")).not.toBeInTheDocument();
  expect(
    screen.getByText(/No active-task or queue count has been inferred/),
  ).toBeVisible();
  await user.click(
    screen.getByRole("button", { name: "Configure measurement" }),
  );
  expect(handlers.onPreparation).toHaveBeenCalledOnce();
  await user.click(screen.getByRole("button", { name: "Open comparison" }));
  expect(handlers.onComparison).toHaveBeenCalledOnce();
});

it("does not plot or promote unmeasured skipped attempts as zero-valued measurements", async () => {
  const user = userEvent.setup();
  setup();
  await user.click(screen.getByRole("button", { name: "Skipped 1" }));
  expect(screen.getByRole("button", { name: "Same program" })).toBeVisible();
  expect(
    screen.getByText("Identical program; no new measurements."),
  ).toBeVisible();
  expect(screen.getAllByText("1 measured attempt")).toHaveLength(2);
  await user.click(screen.getAllByText("1 measured attempt")[0]);
  expect(screen.getByText("10 → 8 ms")).toBeVisible();
  expect(
    screen.queryByText("Same program", {
      selector: ".metric-attempt-list button",
    }),
  ).not.toBeInTheDocument();
});
