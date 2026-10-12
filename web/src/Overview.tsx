import { useState } from "react";
import Icon from "./Icon";
import { Status } from "./Status";
import { display, isObject, label } from "./api";
import type { CampaignDetail, JsonObject } from "./api";

type Props = {
  detail: CampaignDetail;
  onPreparation: () => void;
  onComparison: () => void;
  onExperiment: (id: string) => void;
  onAddHypothesis: () => void;
};
const outcomeFilters = [
  { id: "all", name: "All attempts" },
  { id: "confirmed", name: "Confirmed" },
  { id: "inconclusive", name: "Unresolved" },
  { id: "failed", name: "Failed / constraints" },
  { id: "skipped", name: "Skipped" },
  { id: "historical", name: "Prior context" },
];
const records = (value: unknown): JsonObject[] =>
  Array.isArray(value) ? value.filter(isObject) : [];
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
function number(value: unknown) {
  return finite(value)
    ? value.toLocaleString("en-US", {
        maximumSignificantDigits: Number.isInteger(value) ? 21 : 8,
      })
    : "Not measured";
}
function count(value: unknown) {
  return finite(value) ? value.toLocaleString("en-US") : "—";
}
function gain(value: unknown) {
  return finite(value)
    ? `${value >= 0 ? "+" : ""}${(value * 100).toLocaleString("en-US", { maximumFractionDigits: 4 })}%`
    : "Relative gain unavailable";
}
function gainInterval(value: unknown, unit: unknown) {
  return Array.isArray(value) && value.length === 2 && value.every(finite)
    ? `${number(value[0])} to ${number(value[1])} ${String(unit ?? "")}`
    : "Interval not recorded";
}
function operatorLabel(value: unknown) {
  return value === "lte"
    ? "≤"
    : value === "gte"
      ? "≥"
      : value === "eq"
        ? "="
        : String(value ?? "Threshold not recorded");
}
function matches(attempt: JsonObject, filter: string) {
  if (filter === "all") return true;
  if (filter === "historical") return attempt.current_context === false;
  if (attempt.current_context === false) return false;
  if (filter === "failed")
    return ["failed", "constraint_failed", "invalid_measurement"].includes(
      String(attempt.category),
    );
  if (filter === "inconclusive")
    return ["inconclusive", "promising", "unmeasured"].includes(
      String(attempt.category),
    );
  return attempt.category === filter;
}
function attemptStatus(attempt: JsonObject) {
  if (attempt.category === "skipped") return "skipped";
  if (attempt.status === "failed") return "execution failed";
  if (attempt.category === "invalid_measurement") return "invalid measurement";
  return String(
    attempt.verdict ?? attempt.status ?? attempt.category ?? "not recorded",
  );
}

export default function Overview({
  detail,
  onPreparation,
  onComparison,
  onExperiment,
  onAddHypothesis,
}: Props) {
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const insights = isObject(detail.insights) ? detail.insights : null;
  const summary =
    insights && isObject(insights.summary) ? insights.summary : null;
  const metrics = records(insights?.metrics);
  const tracks = records(insights?.tracks);
  const attempts = tracks.flatMap((track) => records(track.experiments));
  const approved =
    !!detail.protocol_hash &&
    (detail.campaign.approved_hash === detail.protocol_hash ||
      detail.campaign.approved_protocol_hash === detail.protocol_hash);
  const configured =
    !!detail.protocol && Object.keys(detail.protocol).length > 0;
  const readinessReady = detail.readiness?.status === "ready";
  const nextAction = !configured
    ? "Configure measurement"
    : !readinessReady
      ? "Assess readiness"
      : !approved
        ? "Review & approve protocol"
        : null;
  const query = search.trim().toLowerCase();
  const visibleTracks = tracks
    .map((track): JsonObject & { visible: JsonObject[] } => ({
      ...track,
      visible: records(track.experiments).filter(
        (attempt) =>
          matches(attempt, filter) &&
          `${track.title} ${attempt.hypothesis} ${attempt.expected_effect} ${display(attempt.reason)}`
            .toLowerCase()
            .includes(query),
      ),
    }))
    .filter(
      (track) =>
        track.visible.length > 0 ||
        (filter === "all" && !query && !records(track.experiments).length),
    );
  const totals = [
    { key: "attempts", name: "Attempts" },
    { key: "confirmed", name: "Confirmed" },
    { key: "inconclusive", name: "Inconclusive" },
    { key: "constraint_failed", name: "Constraint failures" },
    { key: "failed", name: "Failed" },
    { key: "skipped", name: "Skipped" },
  ];
  return (
    <section className="campaign-overview" aria-label="Campaign overview">
      <div className="section-heading">
        <div>
          <h2>Campaign overview</h2>
          <p>
            {detail.campaign.outcome
              ? `${label(String(detail.campaign.outcome))} · ${label(String(detail.campaign.termination_reason ?? detail.campaign.state))}`
              : "Follow measured results, research tracks, and work in progress."}
          </p>
        </div>
        {nextAction && (
          <button className="button primary" onClick={onPreparation}>
            {nextAction}
            <Icon name="arrow" />
          </button>
        )}
      </div>
      {summary ? (
        <>
          <dl className="outcome-ledger" aria-label="Attempt outcomes">
            {totals.map((item) => (
              <div key={item.key}>
                <dt>{item.name}</dt>
                <dd>{count(summary[item.key])}</dd>
              </div>
            ))}
          </dl>
          <p className="overview-context">
            {count(summary.current)} in current measurement context ·{" "}
            {count(summary.historical)} historical ·{" "}
            {count(summary.measured_runs)} recorded runs across campaign history
          </p>
        </>
      ) : (
        <div className="overview-unavailable">
          <h3>Overview summary unavailable</h3>
          <p>
            The supervisor has not provided an insights projection. Retained
            experiment records and comparison evidence remain available.
          </p>
          <button className="button" onClick={onComparison}>
            Open comparison
            <Icon name="arrow" />
          </button>
        </div>
      )}
      <Scheduler campaign={detail.campaign} onExperiment={onExperiment} />
      <section
        className="overview-performance"
        aria-label="Performance indicators"
      >
        <div className="section-heading">
          <div>
            <h2>Performance</h2>
            <p>
              Best confirmed result per metric, measured against its own
              reference. Constraints remain part of the decision.
            </p>
          </div>
          <button className="button quiet small" onClick={onComparison}>
            Full comparison
            <Icon name="arrow" size={14} />
          </button>
        </div>
        {insights?.available === false && (
          <p className="historical-notice">
            {typeof insights.reason === "string"
              ? insights.reason
              : "A valid measurement protocol is needed before metric insights are available."}
          </p>
        )}
        {metrics.length ? (
          <div className="table-scroll">
            <table
              className="overview-metrics"
              role="table"
              aria-label="Performance measurements"
            >
              <thead role="rowgroup">
                <tr role="row">
                  <th>Metric / direction</th>
                  <th>Reference</th>
                  <th>Confirmed / constraint value</th>
                  <th>Improvement / threshold</th>
                  <th>Evidence / uncertainty</th>
                </tr>
              </thead>
              <tbody role="rowgroup">
                {metrics.map((metric, index) => {
                  const best = isObject(metric.best) ? metric.best : null;
                  const constraint = metric.role === "constraint";
                  const points = records(metric.points);
                  const latestConstraint = constraint ? points.at(-1) : null;
                  const metricSpec = records(detail.protocol?.metrics).find(
                    (spec) => spec.name === metric.name,
                  );
                  const threshold = constraint
                    ? `${operatorLabel(metric.operator ?? metricSpec?.operator)} ${number(metric.threshold ?? metric.value ?? metricSpec?.value)} ${String(metric.unit ?? "")}`
                    : null;
                  return (
                    <tr role="row" key={String(metric.name ?? index)}>
                      <td role="cell" data-label="Metric / direction">
                        <strong className="mono">
                          {String(metric.name ?? "Unnamed metric")}
                        </strong>
                        <span className="metric-unit">
                          {String(metric.unit ?? "Unit not recorded")} ·{" "}
                          {label(String(metric.role ?? "metric"))} ·{" "}
                          {label(String(metric.direction ?? "not recorded"))}
                        </span>
                      </td>
                      <td
                        role="cell"
                        data-label="Reference"
                        className="mono numeric"
                      >
                        {metric.baseline_valid === false &&
                        !best &&
                        !latestConstraint
                          ? "Baseline invalid"
                          : number(
                              latestConstraint?.baseline ??
                                best?.baseline ??
                                metric.baseline,
                            )}
                      </td>
                      <td
                        role="cell"
                        data-label="Confirmed / constraint value"
                        className="mono numeric"
                      >
                        {constraint
                          ? number(latestConstraint?.candidate)
                          : best
                            ? number(best.candidate)
                            : "No confirmed gain"}
                      </td>
                      <td
                        role="cell"
                        data-label="Improvement / threshold"
                        className="mono numeric"
                      >
                        {constraint
                          ? threshold
                          : best
                            ? gain(best.improvement_fraction)
                            : "—"}
                      </td>
                      <td role="cell" data-label="Evidence / uncertainty">
                        {best ? (
                          <>
                            <button
                              className="evidence-link"
                              onClick={() =>
                                onExperiment(String(best.experiment_id))
                              }
                            >
                              {String(
                                best.hypothesis ?? "Review confirmed evidence",
                              )}
                              <Icon name="arrow" size={14} />
                            </button>
                            <span className="metric-gain">
                              95% gain interval:{" "}
                              {gainInterval(best.gain_ci95, metric.unit)}
                            </span>
                            <Provenance
                              value={best.measurement_context_warning}
                            />
                          </>
                        ) : (
                          <span className="muted small">
                            {metric.role === "constraint"
                              ? latestConstraint
                                ? `Latest accepted observation · ${label(String(latestConstraint.verdict ?? latestConstraint.evidence ?? "decision not recorded"))}. Review all constraint decisions below.`
                                : "No accepted constraint observations recorded."
                              : "No current confirmed, resolved improvement recorded."}
                          </span>
                        )}
                        <Provenance
                          value={
                            metric.baseline_context_warning !==
                            best?.measurement_context_warning
                              ? metric.baseline_context_warning
                              : undefined
                          }
                        />
                        {records(metric.points).length > 0 && (
                          <details className="metric-attempts">
                            <summary>
                              {records(metric.points).length} measured attempt
                              {records(metric.points).length === 1 ? "" : "s"}
                            </summary>
                            <MetricAttempts
                              metric={metric}
                              onExperiment={onExperiment}
                            />
                          </details>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="overview-empty">
            <h3>No metric evidence yet</h3>
            <p>
              {configured
                ? "Repeated baseline and candidate measurements will appear here. Unmeasured attempts are listed in their strategy tracks."
                : "Configure a benchmark and target metric, then assess and approve the protocol before running."}
            </p>
            {!configured && (
              <button className="button" onClick={onPreparation}>
                Set up measurement
                <Icon name="arrow" />
              </button>
            )}
          </div>
        )}
      </section>
      <section
        className="overview-tracks"
        aria-label="Research strategy tracks"
      >
        <div className="section-heading">
          <div>
            <h2>Research tracks</h2>
            <p>
              Hypotheses and observed outcomes. Opening evidence does not start
              work.
            </p>
          </div>
          <button className="button" onClick={onAddHypothesis}>
            <Icon name="plus" /> Add hypothesis
          </button>
        </div>
        <div className="track-toolbar">
          <div
            className="outcome-filters"
            aria-label="Filter research outcomes"
          >
            {outcomeFilters.map((item) => (
              <button
                key={item.id}
                className={filter === item.id ? "selected" : ""}
                aria-pressed={filter === item.id}
                onClick={() => setFilter(item.id)}
              >
                {item.name}
                <span>
                  {
                    attempts.filter((attempt) => matches(attempt, item.id))
                      .length
                  }
                </span>
              </button>
            ))}
          </div>
          <label className="search-field">
            <span className="sr-only">Search research tracks</span>
            <input
              type="search"
              placeholder="Search hypotheses or observations"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
        </div>
        {visibleTracks.length ? (
          <div className="strategy-tracks">
            {visibleTracks.map((track, index) => (
              <article
                className="research-track"
                key={String(track.id ?? index)}
              >
                <header className="track-heading">
                  <div>
                    <h3>
                      {String(
                        track.title ?? track.strategy ?? "Research track",
                      )}
                    </h3>
                    {typeof track.strategy === "string" &&
                      track.strategy !== track.title && <p>{track.strategy}</p>}
                  </div>
                  <span className="muted small">
                    {track.visible.length} attempt
                    {track.visible.length === 1 ? "" : "s"}
                  </span>
                </header>
                {(!!track.falsifier ||
                  (Array.isArray(track.tradeoffs) &&
                    track.tradeoffs.length > 0)) && (
                  <details className="track-assumptions">
                    <summary>Falsifier & tradeoffs</summary>
                    {!!track.falsifier && (
                      <p>
                        <strong>Refuted if:</strong> {display(track.falsifier)}
                      </p>
                    )}
                    {Array.isArray(track.tradeoffs) &&
                      track.tradeoffs.length > 0 && (
                        <p>
                          <strong>Tradeoffs:</strong>{" "}
                          {Array.isArray(track.tradeoffs)
                            ? track.tradeoffs.map(String).join("; ")
                            : display(track.tradeoffs)}
                        </p>
                      )}
                  </details>
                )}
                {track.visible.length ? (
                  <ol className="track-attempts">
                    {track.visible.map((attempt) => (
                      <li key={String(attempt.id)}>
                        <div className="attempt-sequence mono">
                          {String(attempt.sequence ?? "—").padStart(2, "0")}
                        </div>
                        <div className="attempt-body">
                          <div className="attempt-heading">
                            <button
                              className="hypothesis-link"
                              onClick={() => onExperiment(String(attempt.id))}
                            >
                              {String(
                                attempt.hypothesis ?? "Untitled hypothesis",
                              )}
                            </button>
                            <Status value={attemptStatus(attempt)} />
                          </div>
                          <div className="attempt-context">
                            <span>
                              {attempt.current_context === false
                                ? "Prior measurement context"
                                : "Current measurement context"}
                            </span>
                            {typeof attempt.phase === "string" && (
                              <span>{label(attempt.phase)}</span>
                            )}
                          </div>
                          <Provenance
                            value={attempt.measurement_context_warning}
                          />
                          <dl className="attempt-observations">
                            <div>
                              <dt>Expected</dt>
                              <dd>
                                {attempt.expected_effect
                                  ? display(attempt.expected_effect)
                                  : "No expected effect recorded."}
                              </dd>
                            </div>
                            <div>
                              <dt>Observed / decision</dt>
                              <dd>
                                {attempt.reason
                                  ? display(attempt.reason)
                                  : ["queued", "running"].includes(
                                        String(attempt.category),
                                      )
                                    ? "Work is in progress; no decision recorded yet."
                                    : "No decision reason recorded."}
                              </dd>
                            </div>
                          </dl>
                          <button
                            className="button quiet small"
                            onClick={() => onExperiment(String(attempt.id))}
                          >
                            Inspect evidence
                            <Icon name="arrow" size={14} />
                          </button>
                        </div>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="track-empty">
                    No hypothesis submitted in this track yet.
                  </p>
                )}
              </article>
            ))}
          </div>
        ) : (
          <div className="overview-empty">
            <h3>
              {tracks.length
                ? "No matching attempts"
                : "No strategy tracks yet"}
            </h3>
            <p>
              {tracks.length
                ? "Choose another outcome or clear the search to inspect the full history."
                : "Assessment can record a strategy plan. Add a manual hypothesis to begin a track without model calls."}
            </p>
            {(query || filter !== "all") && (
              <button
                className="button quiet"
                onClick={() => {
                  setSearch("");
                  setFilter("all");
                }}
              >
                Clear filters
              </button>
            )}
          </div>
        )}
      </section>
    </section>
  );
}

function Provenance({ value }: { value: unknown }) {
  return typeof value === "string" ? (
    <p className="overview-provenance">{value}</p>
  ) : null;
}

function MetricAttempts({
  metric,
  onExperiment,
}: {
  metric: JsonObject;
  onExperiment: Props["onExperiment"];
}) {
  return (
    <div className="metric-attempt-list">
      {records(metric.points).map((point, index) => (
        <div key={String(point.experiment_id ?? index)}>
          <button
            className="evidence-link"
            onClick={() => onExperiment(String(point.experiment_id))}
          >
            {String(
              point.hypothesis ?? `Attempt ${point.sequence ?? index + 1}`,
            )}
          </button>
          <span className="mono">
            {number(point.baseline)} → {number(point.candidate)}{" "}
            {String(metric.unit ?? "")}
          </span>
          <span>
            {label(String(point.evidence ?? point.verdict ?? "not recorded"))} ·{" "}
            {finite(point.improvement_fraction)
              ? `${gain(point.improvement_fraction)} observed`
              : "Relative gain unavailable"}
          </span>
          <span>
            95% gain interval: {gainInterval(point.gain_ci95, metric.unit)}
          </span>
          {point.resolved_improvement === false && (
            <span className="unresolved">Improvement unresolved</span>
          )}
          {point.resolved_regression === true && (
            <span className="regression">Resolved regression</span>
          )}
          <Provenance value={point.measurement_context_warning} />
        </div>
      ))}
    </div>
  );
}

function Scheduler({
  campaign,
  onExperiment,
}: {
  campaign: JsonObject;
  onExperiment: Props["onExperiment"];
}) {
  const scheduler = isObject(campaign.scheduler) ? campaign.scheduler : null;
  const tasks = records(scheduler?.tasks);
  const current = tasks.filter((task) =>
    ["running", "queued", "unknown"].includes(String(task.status)),
  );
  const waiting = current.filter(
    (task) =>
      ["measurement", "confirmation"].includes(String(task.kind)) &&
      task.status === "queued",
  );
  return (
    <section className="scheduler-strip" aria-label="Research scheduling">
      <div className="scheduler-heading">
        <h3>Work in progress</h3>
        <Status value={String(scheduler?.phase ?? "not reported")} />
      </div>
      {scheduler ? (
        <>
          <dl className="scheduler-counts">
            <div>
              <dt>Parallel research preparation</dt>
              <dd>Up to {count(scheduler.max_parallel_tasks)}</dd>
            </div>
            <div>
              <dt>Active tasks</dt>
              <dd>{count(scheduler.active_tasks)}</dd>
            </div>
            <div>
              <dt>Measurement slots</dt>
              <dd>{count(scheduler.measurement_concurrency)} · serialized</dd>
            </div>
            <div>
              <dt>Waiting for measurement</dt>
              <dd>
                {finite(scheduler.waiting_for_measurement)
                  ? scheduler.waiting_for_measurement
                  : waiting.length}
              </dd>
            </div>
          </dl>
          {current.length ? (
            <div className="scheduled-tasks">
              {current.map((task) => (
                <div key={String(task.id)}>
                  <span>
                    <Status value={String(task.status)} />{" "}
                    {label(String(task.kind))}
                  </span>
                  {typeof task.experiment_id === "string" ? (
                    <button
                      className="evidence-link"
                      onClick={() => onExperiment(String(task.experiment_id))}
                    >
                      Inspect attempt
                      <Icon name="arrow" size={14} />
                    </button>
                  ) : (
                    <span className="muted small">
                      {typeof task.strategy_id === "string"
                        ? label(task.strategy_id)
                        : "Campaign task"}
                    </span>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="scheduler-empty">
              {campaign.active_started_at
                ? "Campaign is active and waiting for its next task to be admitted."
                : "No active or queued tasks reported. Opening this page does not start work."}
            </p>
          )}
          <p className="scheduler-note">
            Preparations may overlap. Controlled benchmark execution is
            serialized to preserve comparable measurements.
          </p>
          {tasks.length > current.length && (
            <details className="scheduler-history">
              <summary>
                {tasks.length - current.length} completed task records
              </summary>
              <div className="scheduled-tasks">
                {tasks
                  .filter((task) => !current.includes(task))
                  .map((task) => (
                    <div key={String(task.id)}>
                      <span>
                        {label(String(task.kind))} ·{" "}
                        {label(String(task.status))}
                      </span>
                      {typeof task.experiment_id === "string" && (
                        <button
                          className="evidence-link"
                          onClick={() =>
                            onExperiment(String(task.experiment_id))
                          }
                        >
                          Inspect attempt
                          <Icon name="arrow" size={14} />
                        </button>
                      )}
                    </div>
                  ))}
              </div>
            </details>
          )}
        </>
      ) : (
        <p className="scheduler-empty">
          Scheduler status is unavailable from this supervisor. No active-task
          or queue count has been inferred.
        </p>
      )}
    </section>
  );
}
