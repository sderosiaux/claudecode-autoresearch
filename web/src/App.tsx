import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import Icon from "./Icon";
import Overview from "./Overview";
import { Status, tone } from "./Status";
import {
  campaignList,
  candidateValue,
  comparisonMetrics,
  controlPolicy,
  display,
  isObject,
  label,
  money,
  request,
} from "./api";
import type {
  Budget,
  Campaign,
  CampaignDetail,
  Experiment,
  JsonObject,
  ResearchEvent,
} from "./api";

type Tab =
  | "overview"
  | "preparation"
  | "experiments"
  | "comparison"
  | "activity"
  | "report";
type Action =
  | "assess"
  | "prepare"
  | "profile"
  | "approve"
  | "run"
  | "pause"
  | "stop"
  | "resume"
  | "reconcile";
const tabs: { id: Tab; name: string }[] = [
  { id: "overview", name: "Overview" },
  { id: "preparation", name: "Preparation" },
  { id: "experiments", name: "Experiments" },
  { id: "comparison", name: "Comparison" },
  { id: "activity", name: "Activity" },
  { id: "report", name: "Report & export" },
];

export default function App() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [selected, setSelected] = useState<string | null>(() =>
    new URLSearchParams(window.location.search).get("campaign"),
  );
  const [detail, setDetail] = useState<CampaignDetail | null>(null);
  const [creating, setCreating] = useState(false);
  const [tab, setTab] = useState<Tab>("overview");
  const [hypothesisFormOpen, setHypothesisFormOpen] = useState(false);
  const [selectedExperimentId, setSelectedExperimentId] = useState<
    string | null
  >(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<JsonObject | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(
    () => window.innerWidth >= 900,
  );
  const [campaignSearch, setCampaignSearch] = useState("");
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const navigationToggleRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!sidebarOpen) return;
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && window.innerWidth < 900) {
        setSidebarOpen(false);
        navigationToggleRef.current?.focus();
      }
    };
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [sidebarOpen]);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;

  const loadList = useCallback(async (signal?: AbortSignal) => {
    const rows = campaignList(
      await request<Campaign[] | { campaigns: Campaign[] }>("/campaigns", { signal }),
    );
    if (!signal?.aborted) setCampaigns(rows);
    return rows;
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const interval = window.setInterval(() => {
      if (document.visibilityState === "hidden") return;
      loadList(controller.signal).catch((e) => {
        if (!controller.signal.aborted) setError(errorMessage(e));
      });
    }, 15000);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [loadList]);

  const loadDetail = useCallback(async (id: string, signal?: AbortSignal) => {
    const result = await request<CampaignDetail>(
      `/campaigns/${encodeURIComponent(id)}`,
      { signal },
    );
    if (selectedRef.current === id) {
      setDetail(result);
      setCampaigns((rows) =>
        rows.map((row) => (row.id === id ? result.campaign : row)),
      );
      setUpdatedAt(new Date());
    }
    return result;
  }, []);

  useEffect(() => {
    let active = true;
    Promise.allSettled([loadList(), request<JsonObject>("/capabilities")]).then(
      ([list, caps]) => {
        if (!active) return;
        if (list.status === "rejected") setError(errorMessage(list.reason));
        else if (!selectedRef.current && list.value.length)
          setSelected(list.value[0].id);
        if (caps.status === "fulfilled") setCapabilities(caps.value);
        if (
          !selectedRef.current &&
          (list.status === "rejected" || !list.value.length)
        )
          setLoading(false);
      },
    );
    return () => {
      active = false;
    };
  }, [loadList]);

  useEffect(() => {
    if (!selected) {
      setDetail(null);
      return;
    }
    const controller = new AbortController();
    setDetail(null);
    setLoading(true);
    setError(null);
    loadDetail(selected, controller.signal)
      .catch((e) => {
        if (!controller.signal.aborted) setError(errorMessage(e));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    const interval = window.setInterval(() => {
      if (document.visibilityState === "hidden") return;
      loadDetail(selected, controller.signal).catch((e) => {
        if (!controller.signal.aborted) setError(errorMessage(e));
      });
    }, 3000);
    const url = new URL(window.location.href);
    url.searchParams.set("campaign", selected);
    window.history.replaceState(null, "", url);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [selected, loadDetail]);

  async function refresh() {
    setError(null);
    try {
      await Promise.all([
        loadList(),
        ...(selected ? [loadDetail(selected)] : []),
      ]);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  async function act(action: Action, body?: JsonObject) {
    if (!selected) return;
    setBusy(action);
    setError(null);
    setNotice(null);
    try {
      await request(`/campaigns/${encodeURIComponent(selected)}/${action}`, {
        method: "POST",
        body: JSON.stringify(body ?? {}),
      });
      await Promise.all([loadDetail(selected), loadList()]);
      setNotice(
        action === "reconcile"
          ? "Recovery reconciled. Existing reservations remain counted. Resume is a separate action."
          : action === "approve"
            ? "This protocol version is approved."
            : action === "profile"
              ? "Diagnostic requested. Its results are excluded from comparison."
              : `${label(action)} request accepted by the supervisor.`,
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  function select(id: string) {
    setSelected(id);
    setCreating(false);
    if (window.innerWidth < 900) setSidebarOpen(false);
    setTab("overview");
    setSelectedExperimentId(null);
    setHypothesisFormOpen(false);
    setNotice(null);
  }

  async function saveProtocol(protocol: JsonObject) {
    if (!selected) return;
    setBusy("protocol");
    setError(null);
    try {
      await request(`/campaigns/${encodeURIComponent(selected)}/protocol`, {
        method: "PUT",
        body: JSON.stringify({ protocol }),
      });
      await Promise.all([loadDetail(selected), loadList()]);
      setNotice(
        "Protocol saved. Reassess readiness and approve the new hash before running.",
      );
    } catch (e) {
      setError(errorMessage(e));
      throw e;
    } finally {
      setBusy(null);
    }
  }

  async function create(body: JsonObject) {
    setBusy("create");
    setError(null);
    try {
      const result = await request<Campaign | { campaign: Campaign }>(
        "/campaigns",
        { method: "POST", body: JSON.stringify(body) },
      );
      const campaign =
        "campaign" in result && isObject(result.campaign)
          ? (result.campaign as Campaign)
          : (result as Campaign);
      await loadList();
      select(campaign.id);
      setNotice(
        "Campaign created. Assess readiness before approving a measurement protocol.",
      );
    } catch (e) {
      setError(errorMessage(e));
      throw e;
    } finally {
      setBusy(null);
    }
  }

  const filteredCampaigns = campaigns.filter((campaign) =>
    `${campaign.name} ${campaign.goal} ${campaign.repository}`
      .toLowerCase()
      .includes(campaignSearch.trim().toLowerCase()),
  );
  function newCampaign() {
    setCreating(true);
    if (window.innerWidth < 900) setSidebarOpen(false);
    setNotice(null);
    setError(null);
  }

  return (
    <div
      className={`app-shell ${sidebarOpen ? "navigation-open" : "navigation-closed"}`}
    >
      <a className="skip-link" href="#workspace">
        Skip to workspace
      </a>
      <aside
        id="campaign-navigation"
        className="sidebar"
        aria-label="Campaign navigation"
        hidden={!sidebarOpen}
      >
        <a className="brand" href="/" aria-label="Autoresearch home">
          <span className="brand-mark" aria-hidden="true">
            <Icon name="flask" size={22} />
          </span>
          <span>Autoresearch</span>
        </a>
        <button className="button primary new-campaign" onClick={newCampaign}>
          <Icon name="plus" /> New campaign
        </button>
        <label className="campaign-search">
          <Icon name="search" />
          <span className="sr-only">Search campaigns</span>
          <input
            type="search"
            placeholder="Search campaigns"
            value={campaignSearch}
            onChange={(event) => setCampaignSearch(event.target.value)}
          />
        </label>
        <div className="sidebar-label">
          Campaigns <span>{campaigns.length}</span>
        </div>
        <nav className="campaign-list">
          {filteredCampaigns.map((campaign) => (
            <button
              key={campaign.id}
              className={`campaign-item ${selected === campaign.id && !creating ? "selected" : ""}`}
              onClick={() => select(campaign.id)}
              aria-current={
                selected === campaign.id && !creating ? "page" : undefined
              }
            >
              <Icon name="folder" />
              <span className="campaign-item-text">
                <strong>{campaign.name}</strong>
                <small>
                  <span className={`status-dot ${tone(campaign.state)}`} />
                  {label(campaign.state)}
                </small>
              </span>
            </button>
          ))}
          {!campaigns.length && !loading && (
            <p className="sidebar-empty">Your campaigns will appear here.</p>
          )}
          {campaigns.length > 0 && !filteredCampaigns.length && (
            <div className="sidebar-empty">
              <p role="status">No matching campaigns.</p>
              <button
                className="button quiet small"
                onClick={() => setCampaignSearch("")}
              >
                Clear search
              </button>
            </div>
          )}
        </nav>
        <div className="sidebar-bottom">
          <span className="local-label">
            <span className="status-dot neutral" />
            Local supervisor
          </span>
          <p>Work continues when this page closes.</p>
          <details>
            <summary>
              <Icon name="settings" /> Connector capabilities
            </summary>
            {capabilities ? (
              <JsonView value={capabilities} />
            ) : (
              <p>Unavailable. Refresh to retry.</p>
            )}
          </details>
        </div>
      </aside>
      {sidebarOpen && (
        <button
          className="navigation-backdrop"
          aria-label="Close campaign navigation"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <main id="workspace" className="workspace" tabIndex={-1}>
        <div className="topline">
          <button
            ref={navigationToggleRef}
            className="button icon-button quiet navigation-toggle"
            onClick={() => setSidebarOpen(!sidebarOpen)}
            aria-expanded={sidebarOpen}
            aria-controls="campaign-navigation"
            aria-label={
              sidebarOpen
                ? "Collapse campaign navigation"
                : "Expand campaign navigation"
            }
          >
            <Icon name="panel" />
          </button>
          <div className="context-path">
            <span>Campaigns</span>
            {creating ? (
              <>
                <span className="context-separator">/</span>
                <strong>New campaign</strong>
              </>
            ) : detail ? (
              <>
                <span className="context-separator">/</span>
                <strong>{detail.campaign.name}</strong>
                <span className="context-separator">/</span>
                <span>{tabs.find((item) => item.id === tab)?.name}</span>
              </>
            ) : null}
          </div>
          <button className="button quiet small" onClick={refresh}>
            <Icon name="refresh" size={14} /> Refresh
          </button>
        </div>
        <div className="workspace-body">
          {error && (
            <div role="alert" className="message error">
              <strong>Action needs attention</strong>
              <span>{error}</span>
              <button className="button small" onClick={refresh}>
                Retry connection
              </button>
              <button
                className="dismiss"
                onClick={() => setError(null)}
                aria-label="Dismiss error"
              >
                <Icon name="close" />
              </button>
            </div>
          )}
          {notice && (
            <div role="status" className="message notice">
              <span>{notice}</span>
              <button
                className="dismiss"
                onClick={() => setNotice(null)}
                aria-label="Dismiss notification"
              >
                <Icon name="close" />
              </button>
            </div>
          )}
          {creating ? (
            <CreateCampaign
              onCreate={create}
              onCancel={() => setCreating(false)}
              busy={busy !== null}
              capabilities={capabilities}
            />
          ) : detail ? (
            <>
              <CampaignHeader
                detail={detail}
                busy={busy}
                onAction={act}
                onReview={() => setTab("comparison")}
              />
              {detail.campaign.recovery_required === true && (
                <RecoveryNotice
                  campaign={detail.campaign}
                  busy={busy}
                  onReconcile={() => act("reconcile")}
                />
              )}
              <BudgetRibbon budget={detail.budget} />
              <nav className="tabs" aria-label="Campaign views">
                {tabs.map((item) => (
                  <button
                    key={item.id}
                    className={tab === item.id ? "active" : ""}
                    onClick={() => setTab(item.id)}
                    aria-current={tab === item.id ? "page" : undefined}
                  >
                    {item.name}
                    {item.id === "experiments" && (
                      <span>{detail.experiments.length}</span>
                    )}
                  </button>
                ))}
              </nav>
              <div className="view-content" key={selected}>
                {tab === "overview" && (
                  <Overview
                    detail={detail}
                    onPreparation={() => setTab("preparation")}
                    onComparison={() => setTab("comparison")}
                    onExperiment={(id) => {
                      setSelectedExperimentId(id);
                      setHypothesisFormOpen(false);
                      setTab("experiments");
                    }}
                    onAddHypothesis={() => {
                      setSelectedExperimentId(null);
                      setHypothesisFormOpen(true);
                      setTab("experiments");
                    }}
                  />
                )}
                {tab === "preparation" && (
                  <Preparation
                    detail={detail}
                    busy={busy}
                    onAction={act}
                    onSaveProtocol={saveProtocol}
                  />
                )}
                {tab === "experiments" && (
                  <Experiments
                    detail={detail}
                    onChange={refresh}
                    onError={setError}
                    adding={hypothesisFormOpen}
                    onAddingChange={setHypothesisFormOpen}
                    selectedId={selectedExperimentId}
                    onSelectExperiment={setSelectedExperimentId}
                  />
                )}
                {tab === "comparison" && <Comparison detail={detail} />}
                {tab === "activity" && <Activity events={detail.events} />}
                {tab === "report" && <Report detail={detail} />}
              </div>
              <footer className="workspace-footer">
                <details>
                  <summary>Campaign record</summary>
                  <code>{detail.campaign.id}</code>
                </details>
                <span>
                  {updatedAt
                    ? `Updated ${updatedAt.toLocaleTimeString()}`
                    : "Connecting"}{" "}
                  · Live updates
                </span>
              </footer>
            </>
          ) : loading ? (
            <div className="loading" role="status">
              <span className="spinner" />
              Loading workspace…
            </div>
          ) : (
            <Welcome onCreate={newCampaign} />
          )}
        </div>
      </main>
    </div>
  );
}

function Welcome({ onCreate }: { onCreate: () => void }) {
  return (
    <section className="welcome">
      <span className="welcome-icon">
        <Icon name="flask" size={28} />
      </span>
      <h1>Start a performance campaign</h1>
      <p>
        Connect a repository, define a measurable objective, and compare changes
        under a fixed protocol.
      </p>
      <button className="button primary" onClick={onCreate}>
        <Icon name="plus" /> Create your first campaign
      </button>
      <ol className="welcome-steps">
        <li>
          <strong>Set up</strong>
          <span>Repository, objective, and budget</span>
        </li>
        <li>
          <strong>Approve</strong>
          <span>Review the measurement protocol</span>
        </li>
        <li>
          <strong>Compare</strong>
          <span>Inspect candidates and their evidence</span>
        </li>
      </ol>
    </section>
  );
}

function CreateCampaign({
  onCreate,
  onCancel,
  busy,
  capabilities,
}: {
  onCreate: (body: JsonObject) => Promise<void>;
  onCancel: () => void;
  busy: boolean;
  capabilities: JsonObject | null;
}) {
  const [formError, setFormError] = useState<string | null>(null);
  const [runner, setRunner] = useState("docker");
  const [agent, setAgent] = useState("manual");
  const [enforcement, setEnforcement] = useState("strict");
  const [protocolMode, setProtocolMode] = useState("none");
  const providerCapability =
    agent === "claude_code"
      ? capabilities?.claude_code
      : capabilities?.provider;
  const provider = isObject(providerCapability) ? providerCapability : null;
  const providerBlocked =
    agent !== "manual" &&
    enforcement === "strict" &&
    provider?.strict_budget !== true;
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);
    const data = new FormData(event.currentTarget);
    let protocol: JsonObject | undefined;
    try {
      protocol = protocolFromForm(data, protocolMode);
    } catch (error) {
      setFormError(errorMessage(error));
      return;
    }
    const budget = Number(data.get("budget"));
    if (!Number.isFinite(budget) || budget <= 0) {
      setFormError("Set a finite budget greater than zero.");
      return;
    }
    if (providerBlocked) {
      setFormError(
        "Strict Claude spending enforcement is unavailable. Choose manual research or estimated enforcement.",
      );
      return;
    }
    const goal = String(data.get("goal")).trim();
    const name =
      String(data.get("name") ?? "").trim() ||
      goal.split("\n")[0].slice(0, 120);
    try {
      await onCreate({
        name,
        repository: String(data.get("repository")).trim(),
        goal,
        budget_usd: String(data.get("budget")),
        runner: {
          kind: runner,
          ...(runner === "docker"
            ? {
                image: String(
                  data.get("image") ?? "eclipse-temurin:25-jdk",
                ).trim(),
              }
            : { trusted: true }),
        },
        agent: { kind: agent, enforcement },
        limits: { max_parallel_tasks: Number(data.get("max_parallel_tasks")) },
        ...(protocol ? { protocol } : {}),
      });
    } catch {
      /* Keep all inputs after a rejected API request. */
    }
  }
  return (
    <section className="create-view">
      <div className="section-heading">
        <div>
          <h1>New campaign</h1>
          <p>
            Define what to improve. Nothing runs until you approve a protocol.
          </p>
        </div>
      </div>
      <form onSubmit={submit} className="creation-form">
        <div className="form-main">
          <div className="field objective-field">
            <label htmlFor="goal">Research goal</label>
            <textarea
              id="goal"
              name="goal"
              rows={3}
              required
              maxLength={4000}
              autoFocus
              placeholder="Reduce memory use at the same throughput, while preserving every output record."
            />
            <span className="field-help">
              Name the metric, workload, and behavior to preserve.
            </span>
          </div>
          <div className="field">
            <label htmlFor="repository">Repository path</label>
            <div className="input-with-icon">
              <Icon name="folder" />
              <input
                id="repository"
                name="repository"
                className="mono"
                placeholder="/path/to/local/git/repository"
                required
              />
            </div>
            <span className="field-help">
              Local Git repository accessible to the supervisor.
            </span>
          </div>
          <div className="setup-budget-row">
            <div className="field">
              <label htmlFor="budget">Campaign budget</label>
              <div className="budget-input">
                <span>$</span>
                <input
                  id="budget"
                  name="budget"
                  type="number"
                  min="0.000001"
                  max="100000"
                  step="0.000001"
                  required
                  placeholder="10.00"
                />
                <span>USD</span>
              </div>
            </div>
            <p className="field-help">
              Shared across preparation, experiments, and confirmation. Existing
              compute is excluded.
            </p>
          </div>
          <div className="measurement-setup">
            <div className="field">
              <label htmlFor="protocol-mode">Measurement setup</label>
              <select
                id="protocol-mode"
                value={protocolMode}
                onChange={(event) => setProtocolMode(event.target.value)}
              >
                <option value="none">Configure after creation</option>
                <option value="guided">
                  Use an existing benchmark command
                </option>
                <option value="json">Import a protocol manifest</option>
              </select>
            </div>
            {protocolMode === "guided" && <ProtocolFields prefix="create" />}
            {protocolMode === "json" && (
              <div className="field">
                <label htmlFor="protocol">Protocol manifest (JSON)</label>
                <textarea
                  id="protocol"
                  name="protocol"
                  rows={12}
                  className="mono"
                  spellCheck={false}
                  placeholder={protocolTemplate}
                  required
                />
                <span className="field-help">
                  Commands, metrics, checks, and protected evaluator files are
                  reviewed before approval.
                </span>
              </div>
            )}
          </div>
          <details className="advanced-input">
            <summary>Campaign name</summary>
            <div className="field">
              <label htmlFor="name">Campaign name</label>
              <input
                id="name"
                name="name"
                maxLength={120}
                placeholder="Uses your research goal if left blank"
              />
            </div>
          </details>
          {formError && (
            <p role="alert" className="form-error">
              {formError}
            </p>
          )}
          <div className="form-actions">
            <button
              type="submit"
              className="button primary"
              disabled={busy || providerBlocked}
            >
              {busy ? "Creating…" : "Create campaign"}
              <Icon name="arrow" />
            </button>
            <button
              type="button"
              className="button quiet"
              onClick={onCancel}
              disabled={busy}
            >
              Cancel
            </button>
          </div>
        </div>
        <aside className="creation-aside">
          <h2>
            <Icon name="settings" /> Settings
          </h2>
          <div className="field">
            <label htmlFor="runner">Execution environment</label>
            <select
              id="runner"
              value={runner}
              onChange={(event) => setRunner(event.target.value)}
            >
              <option value="docker">Docker · isolated</option>
              <option value="host">Host · trusted development</option>
            </select>
            <span className="field-help">
              {runner === "docker"
                ? "Requires Docker. No network or credentials by default."
                : "Runs repository commands directly on this machine."}
            </span>
          </div>
          {runner === "docker" ? (
            <details className="advanced-input">
              <summary>Container image</summary>
              <div className="field">
                <label htmlFor="image">Container image</label>
                <input
                  id="image"
                  name="image"
                  className="mono"
                  defaultValue="eclipse-temurin:25-jdk"
                  required
                />
              </div>
            </details>
          ) : (
            <label className="check-label">
              <input type="checkbox" required />
              <span>
                I trust this repository and authorize its commands to execute on
                the supervisor host.
              </span>
            </label>
          )}
          <div className="field">
            <label htmlFor="agent">Research proposals</label>
            <select
              id="agent"
              value={agent}
              onChange={(event) => setAgent(event.target.value)}
            >
              <option value="manual">Manual hypotheses</option>
              <option value="claude_code">
                Claude Code · native exploration
              </option>
              <option value="claude">Claude API · bounded context</option>
            </select>
            <span className="field-help">
              {agent === "manual"
                ? "Submit your own patches. No model calls."
                : agent === "claude_code"
                  ? "Claude Code explores and edits an isolated checkout. The workbench runs the benchmarks and verifies the results. Uses the supervisor's Claude Code login."
                  : "Uses credentials configured on the supervisor."}
            </span>
          </div>
          <div className="field">
            <label htmlFor="max-parallel-tasks">
              Parallel research preparation
            </label>
            <select
              id="max-parallel-tasks"
              name="max_parallel_tasks"
              defaultValue="2"
            >
              <option value="1">1 task at a time</option>
              <option value="2">Up to 2 tasks</option>
              <option value="3">Up to 3 tasks</option>
              <option value="4">Up to 4 tasks</option>
            </select>
            <span className="field-help">
              Proposal and patch preparation can overlap. Measurements run one
              at a time to avoid interference. Manual research makes no model
              calls.
            </span>
          </div>
          {agent !== "manual" && (
            <div className="provider-choice">
              <div className="field">
                <label htmlFor="enforcement">Model spending enforcement</label>
                <select
                  id="enforcement"
                  value={enforcement}
                  onChange={(event) => setEnforcement(event.target.value)}
                >
                  <option value="strict">
                    Strict · verified capability required
                  </option>
                  <option value="estimated">
                    Estimated · costs may exceed estimates
                  </option>
                </select>
              </div>
              {providerBlocked ? (
                <p role="status" className="form-error">
                  {String(
                    provider?.reason ??
                      "Strict Claude spending enforcement is not verified and is disabled.",
                  )}{" "}
                  Choose manual proposals or explicitly select estimated
                  enforcement.
                </p>
              ) : (
                enforcement === "estimated" && (
                  <label className="check-label">
                    <input type="checkbox" required />
                    <span>
                      I understand estimated enforcement is not a guaranteed
                      maximum on provider charges.
                    </span>
                  </label>
                )
              )}
            </div>
          )}
          <div className="setup-note">
            <strong>Before the first run</strong>
            <p>
              Assess readiness, review the protocol, then approve its exact
              version.
            </p>
            <details>
              <summary>Spending scope</summary>
              <p>
                Paid work needs a ledger reservation. A strict provider spending
                bound requires verified capabilities. Infrastructure you already
                own, external services, subscriptions, and taxes may fall
                outside this ledger.
              </p>
            </details>
          </div>
        </aside>
      </form>
    </section>
  );
}

function ProtocolFields({ prefix }: { prefix: string }) {
  return (
    <div className="guided-protocol">
      <div className="field">
        <label htmlFor={`${prefix}-command`}>Benchmark command</label>
        <input
          id={`${prefix}-command`}
          name="benchmark_command"
          className="mono"
          required
          placeholder="sh run.sh --duration 60"
        />
        <span className="field-help">
          Runs with sh -c in the selected runner. Emit JSON metrics and boolean
          correctness checks.
        </span>
      </div>
      <div className="protocol-metric-fields">
        <div className="field">
          <label htmlFor={`${prefix}-metric`}>Objective metric</label>
          <input
            id={`${prefix}-metric`}
            name="metric_name"
            required
            placeholder="process.peak_rss_bytes"
          />
        </div>
        <div className="field">
          <label htmlFor={`${prefix}-unit`}>Unit</label>
          <input
            id={`${prefix}-unit`}
            name="metric_unit"
            required
            placeholder="bytes"
          />
        </div>
        <div className="field">
          <label htmlFor={`${prefix}-direction`}>Direction</label>
          <select id={`${prefix}-direction`} name="metric_direction">
            <option value="minimize">Minimize</option>
            <option value="maximize">Maximize</option>
          </select>
        </div>
      </div>
      <div className="field">
        <label htmlFor={`${prefix}-protected`}>Protected evaluator paths</label>
        <textarea
          id={`${prefix}-protected`}
          name="protected_paths"
          rows={2}
          className="mono"
          placeholder={"run.sh\nbenchmarks/"}
        />
        <span className="field-help">
          One repository path per line. Candidate patches cannot change these
          files.
        </span>
      </div>
      <details className="advanced-input">
        <summary>Measurement settings</summary>
        <div className="protocol-metric-fields">
          <div className="field">
            <label htmlFor={`${prefix}-repetitions`}>
              Independent repetitions
            </label>
            <input
              id={`${prefix}-repetitions`}
              name="repetitions"
              type="number"
              min={3}
              max={50}
              defaultValue={3}
              required
            />
          </div>
          <div className="field">
            <label htmlFor={`${prefix}-timeout`}>
              Timeout per run (seconds)
            </label>
            <input
              id={`${prefix}-timeout`}
              name="timeout_seconds"
              type="number"
              min={1}
              max={3600}
              defaultValue={60}
              required
            />
          </div>
          <div className="field">
            <label htmlFor={`${prefix}-gain`}>Minimum useful gain (%)</label>
            <input
              id={`${prefix}-gain`}
              name="minimum_improvement"
              type="number"
              min={0}
              max={100}
              step="any"
              defaultValue={2}
              required
            />
          </div>
        </div>
        <p className="field-help">
          Confirmation uses the same number of new independent runs. Import JSON
          for additional metrics, hard constraints, or environment variables.
        </p>
      </details>
    </div>
  );
}

function protocolFromForm(
  data: FormData,
  mode: string,
): JsonObject | undefined {
  if (mode === "none") return undefined;
  if (mode === "json") {
    try {
      const parsed: unknown = JSON.parse(String(data.get("protocol")));
      if (!isObject(parsed)) throw new Error("Expected a JSON object.");
      return parsed;
    } catch (error) {
      throw new Error(`Check the protocol JSON: ${errorMessage(error)}`);
    }
  }
  return {
    command: ["sh", "-c", String(data.get("benchmark_command")).trim()],
    metrics: [
      {
        name: String(data.get("metric_name")).trim(),
        unit: String(data.get("metric_unit")).trim(),
        direction: String(data.get("metric_direction")),
        role: "objective",
        min_repetitions: 3,
        minimum_improvement: Number(data.get("minimum_improvement")) / 100,
      },
    ],
    repetitions: Number(data.get("repetitions")),
    confirmation_repetitions: Number(data.get("repetitions")),
    timeout_seconds: Number(data.get("timeout_seconds")),
    protected_paths: String(data.get("protected_paths") ?? "")
      .split("\n")
      .map((path) => path.trim())
      .filter(Boolean),
  };
}

function CampaignHeader({
  detail,
  busy,
  onAction,
  onReview,
}: {
  detail: CampaignDetail;
  busy: string | null;
  onAction: (action: Action) => void;
  onReview: () => void;
}) {
  const campaign = detail.campaign;
  const approved =
    !!detail.protocol_hash &&
    (campaign.approved_hash === detail.protocol_hash ||
      campaign.approved_protocol_hash === detail.protocol_hash);
  const recoveryRequired = campaign.recovery_required === true;
  const policy = controlPolicy(
    campaign.state,
    approved,
    !!campaign.reconciliation && !recoveryRequired,
  );
  const reviewable = campaign.state === "reviewable";
  return (
    <header className="campaign-header">
      <div className="campaign-title">
        <div className="campaign-heading-line">
          <h1>{campaign.name}</h1>
          <Status
            value={
              approved && campaign.state === "awaiting_approval"
                ? "protocol approved"
                : campaign.state
            }
          />
        </div>
        <p>{campaign.goal}</p>
        <details className="campaign-context">
          <summary>
            <Icon name="folder" size={14} />
            Repository & record
          </summary>
          <dl className="compact-definition">
            <div>
              <dt>Repository</dt>
              <dd className="mono">{campaign.repository}</dd>
            </div>
            <div>
              <dt>Campaign ID</dt>
              <dd className="mono">{campaign.id}</dd>
            </div>
            <div>
              <dt>Protocol hash</dt>
              <dd className="mono">
                {detail.protocol_hash ?? "Not configured"}
              </dd>
            </div>
          </dl>
        </details>
        {campaign.termination_reason && (
          <p className="termination">
            <strong>{label(campaign.outcome ?? campaign.state)}</strong>
            <span> · {label(campaign.termination_reason)}</span>
          </p>
        )}
      </div>
      <div className="run-controls">
        <div className="control-buttons">
          {reviewable && (
            <button className="button primary" onClick={onReview}>
              Review results
              <Icon name="arrow" />
            </button>
          )}
          {policy.resume ? (
            <button
              className="button primary"
              disabled={!!busy || recoveryRequired}
              onClick={() => onAction("resume")}
            >
              <Icon name="play" />
              {busy === "resume" ? "Resuming…" : "Resume"}
            </button>
          ) : (
            <button
              className={`button ${reviewable ? "quiet" : "primary"}`}
              disabled={!!busy || recoveryRequired || !policy.run}
              onClick={() => onAction("run")}
              title={
                recoveryRequired
                  ? "Reconcile interrupted work before running."
                  : !policy.run
                    ? "Approve a ready protocol before running."
                    : undefined
              }
            >
              <Icon name="play" />
              {busy === "run"
                ? "Starting…"
                : reviewable
                  ? "Run again"
                  : "Run campaign"}
            </button>
          )}
          <button
            className="button"
            disabled={!!busy || !policy.pause}
            onClick={() => onAction("pause")}
          >
            <Icon name="pause" />
            Pause
          </button>
          <button
            className="button danger-quiet"
            disabled={!!busy || !policy.stop}
            onClick={() => onAction("stop")}
          >
            <Icon name="stop" />
            Stop
          </button>
        </div>
        <details className="control-limits">
          <summary>Controls & limits</summary>
          <p className="control-help">
            {recoveryRequired
              ? "Reconcile interrupted work before resuming."
              : "Pause holds new admissions. Stop interrupts active work."}
          </p>
          {isObject(campaign.limits) && (
            <dl>
              <div>
                <dt>Experiments</dt>
                <dd>{display(campaign.limits.max_experiments)}</dd>
              </div>
              <div>
                <dt>Active duration</dt>
                <dd>{display(campaign.limits.max_active_seconds)}s</dd>
              </div>
              <div>
                <dt>Task timeout</dt>
                <dd>{display(campaign.limits.max_task_seconds)}s</dd>
              </div>
              <div>
                <dt>Active time used</dt>
                <dd>
                  {typeof campaign.active_seconds === "number"
                    ? `${campaign.active_seconds.toFixed(1)}s`
                    : "Not recorded"}
                </dd>
              </div>
            </dl>
          )}
        </details>
      </div>
    </header>
  );
}

function RecoveryNotice({
  campaign,
  busy,
  onReconcile,
}: {
  campaign: Campaign;
  busy: string | null;
  onReconcile: () => void;
}) {
  return (
    <section
      className="recovery-notice"
      aria-label="Coordinator recovery required"
    >
      <div>
        <h2>Reconcile interrupted work before resuming.</h2>
        <p>
          {campaign.cleanup_warning
            ? "A task could not confirm that its resources were removed. "
            : "The coordinator restarted with uncertain executions. "}
          Reconciliation
          checks that the persisted Docker or provider watchdog lease has
          expired, marks unknown experiments cancelled and inconclusive, and
          retains their spending reservations. It does not start new work.
        </p>
        {isObject(campaign.runner) && campaign.runner.kind === "host" && (
          <p className="muted">
            Trusted host recovery stays blocked because execution isolation cannot
            be verified.
          </p>
        )}
        {typeof campaign.resource_lease_expires_at === "string" && (
          <p className="field-help">
            Resource deadline: <time dateTime={campaign.resource_lease_expires_at}>
              {campaign.resource_lease_expires_at}
            </time>
          </p>
        )}
        {(campaign.recovery_reason ?? campaign.cleanup_warning) != null && (
          <p className="recovery-reason">
            <strong>Recovery reason:</strong>{" "}
            {display(campaign.recovery_reason ?? campaign.cleanup_warning)}
          </p>
        )}
      </div>
      <button className="button" disabled={!!busy} onClick={onReconcile}>
        {busy === "reconcile" ? "Reconciling…" : "Reconcile recovery"}
      </button>
    </section>
  );
}

export function BudgetRibbon({ budget }: { budget: Budget }) {
  const segments = [
    { key: "consumed", name: "Consumed" },
    { key: "engaged", name: "Engaged" },
    { key: "protected", name: "Protected" },
    { key: "available", name: "Available" },
  ] as const;
  return (
    <section className="budget-ribbon" aria-label="Campaign budget">
      <div className="budget-limit">
        <span>Limit</span>
        <strong title={`${budget.limit} micro-USD`}>
          {money(budget.limit)}
        </strong>
      </div>
      <dl className="budget-numbers">
        {segments.map((segment) => (
          <div key={segment.key}>
            <dt
              title={
                segment.key === "protected"
                  ? "Protected to finish confirmation, reporting, and cleanup"
                  : undefined
              }
            >
              {segment.name}
            </dt>
            <dd title={`${budget[segment.key]} micro-USD`}>
              {money(budget[segment.key])}
            </dd>
          </div>
        ))}
      </dl>
      <details className="budget-scope">
        <summary>
          {label(budget.enforcement ?? "Not reported")}
          <Icon name="chevron" size={13} />
          <span className="sr-only"> · Ledger scope</span>
        </summary>
        <div>
          <strong>
            {label(budget.enforcement ?? "Enforcement not reported")} accounting
          </strong>
          <p>{display(budget.scope)}</p>
          <p>
            Consumed can include a full session allowance retained conservatively.
            These amounts are not verified provider charges.
          </p>
          <p>
            Protected funds cover finishing work. Amounts use integer micro-USD.
          </p>
        </div>
      </details>
    </section>
  );
}

function Preparation({
  detail,
  busy,
  onAction,
  onSaveProtocol,
}: {
  detail: CampaignDetail;
  busy: string | null;
  onAction: (action: Action, body?: JsonObject) => void;
  onSaveProtocol: (protocol: JsonObject) => Promise<void>;
}) {
  const [reviewedHash, setReviewedHash] = useState<string | null>(null);
  const readiness = detail.readiness;
  const readinessStatus = readiness
    ? String(readiness.status ?? readiness.state ?? "assessed")
    : "not assessed";
  const approvedHash =
    detail.campaign.approved_protocol_hash ?? detail.campaign.approved_hash;
  const approved = Boolean(
    detail.protocol_hash && approvedHash === detail.protocol_hash,
  );
  const observations =
    readiness?.observations ?? readiness?.findings ?? readiness?.checks;
  return (
    <div className="preparation-layout">
      <section>
        <div className="section-heading">
          <div>
            <div className="eyebrow">01 / READINESS</div>
            <h2>Readiness</h2>
            <p>
              Check the workload, measurement coverage, and missing
              prerequisites.
            </p>
          </div>
          <Status value={readinessStatus} />
        </div>
        <div className="readiness-body">
          {readiness ? (
            <>
              <EvidenceList value={observations} />
              {readiness.coverage && (
                <p className="readiness-coverage">
                  {display(readiness.coverage)}
                </p>
              )}
              {readiness.summary && <p>{display(readiness.summary)}</p>}
              {readiness.reason && <p>{display(readiness.reason)}</p>}
              {readiness.blockers && (
                <EvidenceList value={readiness.blockers} />
              )}
              {readiness.plan && (
                <div className="evidence-section">
                  <h3>Preparation plan</h3>
                  <EvidenceList value={readiness.plan} />
                </div>
              )}
              <details className="disclosure">
                <summary>Full readiness evidence</summary>
                <JsonView value={readiness} />
              </details>
            </>
          ) : (
            <Empty
              title="Readiness not assessed"
              text="Inspect build commands, workload, correctness checks, and measurement coverage before exploring optimizations."
            />
          )}
        </div>
        <div className="inline-actions">
          <button
            className="button primary"
            disabled={!!busy}
            onClick={() => onAction("assess")}
          >
            {busy === "assess"
              ? "Assessing…"
              : readiness
                ? "Reassess readiness"
                : "Assess readiness"}
          </button>
          <button
            className="button"
            disabled={!!busy}
            onClick={() => onAction("prepare")}
          >
            {busy === "prepare" ? "Preparing…" : "Prepare measurement"}
          </button>
        </div>
        <div className="section-divider" />
        <div className="section-heading">
          <div>
            <div className="eyebrow">02 / PROTOCOL REVIEW</div>
            <h2>Measurement protocol</h2>
            <p>
              Review the command, metrics, and protected evaluator before
              approving.
            </p>
          </div>
          {approved && <Status value="approved" />}
        </div>
        {detail.protocol && Object.keys(detail.protocol).length ? (
          <>
            <ProtocolSummary protocol={detail.protocol} />
            <details className="disclosure">
              <summary>Inspect complete protocol manifest</summary>
              <JsonView value={detail.protocol} />
            </details>
            <details className="protocol-hash">
              <summary>Exact protocol hash</summary>
              <code>{detail.protocol_hash ?? "Not generated yet"}</code>
            </details>
            {!approved && (
              <div className="approval-box">
                <label className="check-label">
                  <input
                    type="checkbox"
                    checked={
                      reviewedHash === detail.protocol_hash &&
                      !!detail.protocol_hash
                    }
                    onChange={(e) =>
                      setReviewedHash(
                        e.target.checked ? detail.protocol_hash : null,
                      )
                    }
                  />{" "}
                  <span>
                    I reviewed the workload, commands, metrics, and correctness
                    checks in this protocol.
                  </span>
                </label>
                <button
                  className="button primary"
                  disabled={
                    !!busy ||
                    !detail.protocol_hash ||
                    reviewedHash !== detail.protocol_hash ||
                    readinessStatus !== "ready"
                  }
                  onClick={() =>
                    onAction("approve", { protocol_hash: detail.protocol_hash })
                  }
                >
                  {busy === "approve" ? "Approving…" : "Approve this protocol"}{" "}
                  <Icon name="arrow" />
                </button>
                {readinessStatus !== "ready" && (
                  <p className="field-help">
                    A ready assessment is required before approval.
                  </p>
                )}
              </div>
            )}
          </>
        ) : (
          <Empty
            title="No measurement protocol yet"
            text="Configure your existing benchmark below, import a manifest, or use Prepare measurement to add missing instrumentation."
          />
        )}
        <ProtocolEditor
          protocol={detail.protocol}
          busy={!!busy}
          onSave={onSaveProtocol}
        />
        <div className="section-divider" />
        <Diagnostics detail={detail} busy={busy} onAction={onAction} />
      </section>
      <aside className="process-note">
        <h3>Campaign settings</h3>
        <dl className="settings-list">
          <div>
            <dt>Runner</dt>
            <dd>
              {isObject(detail.campaign.runner)
                ? label(String(detail.campaign.runner.kind))
                : "Not recorded"}
            </dd>
          </div>
          <div>
            <dt>Research proposals</dt>
            <dd>
              {isObject(detail.campaign.agent)
                ? label(String(detail.campaign.agent.kind))
                : "Not recorded"}
            </dd>
          </div>
          <div>
            <dt>Accounting</dt>
            <dd>{label(detail.budget.enforcement)}</dd>
          </div>
        </dl>
        {isObject(detail.campaign.runner) &&
          typeof detail.campaign.runner.image === "string" && (
            <details className="disclosure">
              <summary>Runner image</summary>
              <code className="settings-image">
                {detail.campaign.runner.image}
              </code>
            </details>
          )}
        <div className="setup-note">
          <strong>
            {approved ? "Protocol approved" : "Approval required"}
          </strong>
          <p>
            {approved
              ? "Changes to this protocol need a new review and approval."
              : "Approve the exact measurement protocol before starting a run."}
          </p>
          {approved &&
            isObject(detail.campaign.approval) &&
            detail.campaign.approval.authority === "delegated_agent" && (
              <p>
                Approved under operator delegation:{" "}
                {display(detail.campaign.approval.rationale)}
              </p>
            )}
        </div>
      </aside>
    </div>
  );
}

const protocolTemplate = JSON.stringify(
  {
    command: ["./benchmark.sh"],
    metrics: [
      {
        name: "duration_ms",
        unit: "ms",
        direction: "minimize",
        role: "objective",
      },
    ],
    repetitions: 3,
    confirmation_repetitions: 3,
    timeout_seconds: 60,
    protected_paths: ["benchmark.sh"],
  },
  null,
  2,
);

function ProtocolEditor({
  protocol,
  busy,
  onSave,
}: {
  protocol: JsonObject | null;
  busy: boolean;
  onSave: (protocol: JsonObject) => Promise<void>;
}) {
  const hasProtocol = !!protocol && Object.keys(protocol).length > 0;
  const [editing, setEditing] = useState(false);
  const [mode, setMode] = useState("guided");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    try {
      const fields = new FormData(event.currentTarget);
      if (mode === "json") fields.set("protocol", text);
      const parsed = protocolFromForm(fields, mode);
      if (!parsed) throw new Error("Define a measurement protocol.");
      await onSave(parsed);
      setEditing(false);
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  return (
    <div className="protocol-editor">
      {!editing ? (
        <button
          className="button"
          onClick={() => {
            setText(
              hasProtocol
                ? JSON.stringify(protocol, null, 2)
                : protocolTemplate,
            );
            setMode(hasProtocol ? "json" : "guided");
            setError(null);
            setEditing(true);
          }}
        >
          {hasProtocol
            ? "Edit protocol manifest"
            : "Configure measurement protocol"}
        </button>
      ) : (
        <form onSubmit={submit}>
          <h3>
            {hasProtocol
              ? "Propose a revised protocol"
              : "Define the measurement protocol"}
          </h3>
          <p className="muted small">
            Saving creates a new protocol hash and clears approval. Reassess
            readiness before approving.
          </p>
          {!hasProtocol && (
            <div className="field protocol-editor-mode">
              <label htmlFor="protocol-editor-mode">Measurement setup</label>
              <select
                id="protocol-editor-mode"
                value={mode}
                onChange={(event) => setMode(event.target.value)}
              >
                <option value="guided">
                  Use an existing benchmark command
                </option>
                <option value="json">Import a protocol manifest</option>
              </select>
            </div>
          )}
          {mode === "guided" ? (
            <ProtocolFields prefix="prepare" />
          ) : (
            <div className="field">
              <label htmlFor="protocol-editor">Protocol manifest (JSON)</label>
              <textarea
                id="protocol-editor"
                rows={16}
                className="mono"
                value={text}
                onChange={(e) => setText(e.target.value)}
                spellCheck={false}
                required
              />
            </div>
          )}
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
          <div className="inline-actions">
            <button className="button primary" disabled={busy}>
              {busy ? "Saving…" : "Save protocol"}
            </button>
            <button
              className="button quiet"
              type="button"
              disabled={busy}
              onClick={() => setEditing(false)}
            >
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

function ProtocolSummary({ protocol }: { protocol: JsonObject }) {
  const entries = Object.entries(protocol).filter(([key]) =>
    [
      "benchmark_command",
      "check_command",
      "build_command",
      "command",
      "metrics",
      "scenario",
      "repetitions",
      "warmup_runs",
      "timeout_seconds",
      "correctness_command",
    ].includes(key),
  );
  return entries.length ? (
    <dl className="protocol-summary">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{label(key)}</dt>
          <dd>
            {key === "metrics" && Array.isArray(value) ? (
              <div className="protocol-metrics">
                {value.filter(isObject).map((metric, index) => (
                  <div key={index}>
                    <strong className="mono">{String(metric.name)}</strong>
                    <span>
                      {String(metric.unit)} · {label(String(metric.direction))}{" "}
                      · {label(String(metric.role ?? "objective"))}
                      {metric.operator
                        ? ` · ${metric.operator} ${metric.value}`
                        : ""}
                    </span>
                    <small>
                      Minimum {String(metric.min_repetitions ?? 3)} runs · {metric.role === "info"
                        ? "variation is informational"
                        : `noise ceiling ${typeof metric.max_cv === "number"
                          ? `${metric.max_cv * 100}%`
                          : "not specified"}`}
                      {typeof metric.minimum_improvement === "number" &&
                      metric.role === "objective"
                        ? ` · useful gain ≥ ${metric.minimum_improvement * 100}%`
                        : ""}
                    </small>
                  </div>
                ))}
              </div>
            ) : Array.isArray(value) &&
              value.every((v) => typeof v === "string") ? (
              <code>{value.join(" ")}</code>
            ) : (
              <pre>{display(value)}</pre>
            )}
          </dd>
        </div>
      ))}
    </dl>
  ) : null;
}

function Experiments({
  detail,
  onChange,
  onError,
  selectedId,
  onSelectExperiment: setSelectedId,
  adding,
  onAddingChange: setAdding,
}: {
  detail: CampaignDetail;
  onChange: () => Promise<void>;
  onError: (error: string | null) => void;
  adding: boolean;
  onAddingChange: (adding: boolean) => void;
  selectedId: string | null;
  onSelectExperiment: (id: string | null) => void;
}) {
  const [filter, setFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");

  const [submitting, setSubmitting] = useState(false);
  const experiments = detail.experiments;
  const selected = experiments.find((e) => e.id === selectedId);
  const statuses = [...new Set(experiments.map((e) => experimentOutcome(e)))];
  const filtered = experiments.filter(
    (e) =>
      (statusFilter === "all" || experimentOutcome(e) === statusFilter) &&
      `${e.hypothesis} ${e.strategy ?? ""} ${e.id}`
        .toLowerCase()
        .includes(filter.toLowerCase()),
  );
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    onError(null);
    const form = new FormData(event.currentTarget);
    try {
      await request(
        `/campaigns/${encodeURIComponent(detail.campaign.id)}/experiments`,
        {
          method: "POST",
          body: JSON.stringify({
            hypothesis: String(form.get("hypothesis")).trim(),
            strategy: String(form.get("strategy")).trim(),
            expected_effect: String(form.get("expected_effect")).trim(),
            patch: String(form.get("patch")),
            ...(form.get("parent_id")
              ? { parent_id: form.get("parent_id") }
              : {}),
          }),
        },
      );
      await onChange();
      setAdding(false);
    } catch (e) {
      onError(errorMessage(e));
    } finally {
      setSubmitting(false);
    }
  }
  return (
    <>
      <StrategyPlan value={detail.campaign.strategy_plan} />
      <div className="section-heading">
        <div>
          <div className="eyebrow">RESEARCH RECORD</div>
          <h2>Experiments</h2>
          <p>Hypotheses, outcomes, and retained evidence for every attempt.</p>
        </div>
        <button className="button primary" onClick={() => setAdding(!adding)}>
          {adding ? "Close form" : "Add hypothesis"}
        </button>
      </div>
      {adding && (
        <form className="hypothesis-form" onSubmit={submit}>
          <h3>Propose a manual experiment</h3>
          <div className="field">
            <label htmlFor="hypothesis">Testable hypothesis</label>
            <textarea
              id="hypothesis"
              name="hypothesis"
              required
              rows={3}
              placeholder="Explain the observed cost, the change, and the result that would refute the hypothesis."
            />
          </div>
          <div className="two-fields">
            <div className="field">
              <label htmlFor="strategy">Strategy</label>
              <input
                id="strategy"
                name="strategy"
                required
                placeholder="Allocation reduction, batching, I/O…"
              />
            </div>
            <div className="field">
              <label htmlFor="parent_id">Starting point</label>
              <select id="parent_id" name="parent_id">
                <option value="">Approved baseline</option>
                {experiments.map((e) => (
                  <option key={e.id} value={e.id} disabled={!e.revision}>
                    {e.id.slice(0, 12)} · {e.hypothesis.slice(0, 60)}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="field">
            <label htmlFor="expected_effect">Expected effect</label>
            <input
              id="expected_effect"
              name="expected_effect"
              placeholder="Which metric should change, and why?"
            />
          </div>
          <div className="field">
            <label htmlFor="patch">Unified diff</label>
            <textarea
              id="patch"
              name="patch"
              rows={10}
              required
              className="mono"
              spellCheck={false}
              placeholder="diff --git a/src/Example.java b/src/Example.java"
            />
            <span className="field-help">
              The supervisor applies and evaluates the change in an isolated
              worktree under the approved protocol.
            </span>
          </div>
          <div className="inline-actions">
            <button className="button primary" disabled={submitting}>
              {submitting ? "Adding…" : "Add experiment"}
            </button>
            <button
              className="button quiet"
              type="button"
              disabled={submitting}
              onClick={() => setAdding(false)}
            >
              Cancel
            </button>
          </div>
        </form>
      )}
      {experiments.length ? (
        <>
          <div className="table-toolbar">
            <label className="search-field">
              <span className="sr-only">Search experiments</span>
              <input
                placeholder="Search hypotheses or strategies…"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              />
            </label>
            <label>
              <span className="sr-only">Experiment status</span>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
              >
                <option value="all">All statuses</option>
                {statuses.map((s) => (
                  <option key={s} value={s}>
                    {label(s)}
                  </option>
                ))}
              </select>
            </label>
            <span className="muted small">
              {filtered.length} of {experiments.length}
            </span>
          </div>
          <div className="experiments-layout">
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Hypothesis / strategy</th>
                    <th>Evidence status</th>
                    <th>Parent</th>
                    <th>Review</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((experiment, i) => (
                    <tr
                      key={experiment.id}
                      className={
                        selectedId === experiment.id ? "row-selected" : ""
                      }
                    >
                      <td>
                        <span className="experiment-number">
                          {String(i + 1).padStart(2, "0")}
                        </span>
                        <strong>{experiment.hypothesis}</strong>
                        <small>
                          {experiment.strategy || "Strategy not recorded"}
                        </small>
                      </td>
                      <td>
                        <Status value={experimentOutcome(experiment)} />
                        {experiment.protocol_hash !== detail.protocol_hash && (
                          <small className="prior-protocol-label">
                            Prior protocol
                          </small>
                        )}
                      </td>
                      <td className="mono small">
                        {experiment.parent_id?.slice(0, 8) ?? "baseline"}
                      </td>
                      <td>
                        <button
                          className="button quiet small"
                          onClick={() => setSelectedId(experiment.id)}
                          aria-label={`Review evidence for ${experiment.hypothesis}`}
                        >
                          View evidence
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!filtered.length && (
                <Empty
                  title="No matching experiments."
                  text="Try another search or status filter."
                />
              )}
            </div>
          </div>
          {selected && (
            <ExperimentDetail
              key={selected.id}
              campaignId={detail.campaign.id}
              experiment={selected}
              onSelectExperiment={setSelectedId}
              currentProtocolHash={detail.protocol_hash}
              onClose={() => setSelectedId(null)}
            />
          )}
        </>
      ) : (
        <Empty
          title="No experiments yet"
          text="Run the approved campaign for supervised exploration, or add your own patch with a testable explanation. Baseline measurements and new experiments will appear here."
        />
      )}
    </>
  );
}

function experimentOutcome(experiment: Experiment): string {
  return experiment.termination_reason === "duplicate_program"
    ? "Duplicate program · skipped"
    : String(
        experiment.verdict ??
          experiment.status ??
          experiment.state ??
          "proposed",
      );
}

function StrategyPlan({ value }: { value: unknown }) {
  const entries = Array.isArray(value)
    ? value
    : isObject(value) && Array.isArray(value.strategies)
      ? value.strategies
      : [];
  return (
    <section className="strategy-plan">
      <div className="eyebrow">EXPLORATION PLAN</div>
      <h3>Strategy plan</h3>
      {entries.length ? (
        entries.map((entry, index) => (
          <details className="strategy-entry" key={index}>
            <summary>
              {isObject(entry)
                ? String(entry.title ?? entry.id ?? `Strategy ${index + 1}`)
                : display(entry)}
            </summary>
            <EvidenceList value={entry} />
          </details>
        ))
      ) : (
        <p className="muted small">
          No strategy plan has been recorded. An assessment can identify
          applicable techniques; each experiment still needs its own testable
          hypothesis and patch.
        </p>
      )}
    </section>
  );
}

function ExperimentDetail({
  campaignId,
  experiment,
  onClose,
  onSelectExperiment,
  currentProtocolHash,
}: {
  campaignId: string;
  experiment: Experiment;
  currentProtocolHash: string | null;
  onClose: () => void;
  onSelectExperiment: (id: string) => void;
}) {
  const duplicate = experiment.termination_reason === "duplicate_program";
  const priorProtocol = experiment.protocol_hash !== currentProtocolHash;
  const observed = duplicate
    ? experiment.duplicate_reason
    : (experiment.observations ??
      experiment.reason ??
      experiment.decision_reason ??
      experiment.error ??
      (isObject(experiment.confirmation)
        ? experiment.confirmation.reasons
        : isObject(experiment.comparison)
          ? experiment.comparison.reasons
          : experiment.result));
  const metrics = duplicate
    ? undefined
    : Object.keys(comparisonMetrics(experiment)).length
      ? comparisonMetrics(experiment)
      : (experiment.metrics ??
        (isObject(experiment.result) ? experiment.result.metrics : undefined));
  const runs = duplicate
    ? undefined
    : (experiment.runs ?? experiment.executions);
  return (
    <section className="experiment-detail" aria-label="Experiment evidence">
      <div className="section-heading">
        <div>
          <span className="eyebrow mono">EXPERIMENT {experiment.id}</span>
          <h2>{experiment.hypothesis}</h2>
        </div>
        <button
          className="button quiet"
          onClick={onClose}
          aria-label="Close experiment detail"
        >
          Close ×
        </button>
      </div>
      <>
        {priorProtocol && (
          <p className="historical-notice">
            Prior protocol: these observations belong to an earlier measurement
            context and are retained as history. They are not current comparison
            results.
          </p>
        )}
      </>
      <div className="evidence-columns">
        <div>
          <h3>Hypothesis</h3>
          <p>{experiment.hypothesis}</p>
          <dl className="compact-definition">
            <div>
              <dt>Strategy</dt>
              <dd>{experiment.strategy ?? "Not recorded"}</dd>
            </div>
            <div>
              <dt>Proposal origin</dt>
              <dd>
                {typeof experiment.origin === "string"
                  ? label(experiment.origin)
                  : "Not recorded"}
              </dd>
            </div>
            <div>
              <dt>Expected effect</dt>
              <dd>{display(experiment.expected_effect)}</dd>
            </div>
            <div>
              <dt>Parent</dt>
              <dd className="mono">
                {experiment.parent_id ?? "Approved baseline"}
              </dd>
            </div>
          </dl>
        </div>
        <div>
          <h3>Observed evidence</h3>
          <Status value={experimentOutcome(experiment)} />
          <EvidenceList value={observed} />
          {duplicate && typeof experiment.duplicate_of === "string" && (
            <button
              className="button small"
              onClick={() =>
                onSelectExperiment(String(experiment.duplicate_of))
              }
            >
              View source attempt
              <Icon name="arrow" size={14} />
            </button>
          )}
          {metrics !== undefined && (
            <div className="evidence-section">
              <h3>Measured values</h3>
              <MetricTable value={metrics} />
            </div>
          )}
        </div>
      </div>
      <details className="disclosure" open>
        <summary>Patch evaluated</summary>
        {experiment.patch ? (
          <pre className="diff">{experiment.patch}</pre>
        ) : (
          <p className="muted">No patch was recorded for this experiment.</p>
        )}
      </details>
      {runs !== undefined && (
        <details className="disclosure" open>
          <summary>Candidate exploration runs, checks, and logs</summary>
          <RunRecords campaignId={campaignId} value={runs} />
        </details>
      )}
      {(
        [
          ["reference_runs", "Reference exploration runs"],
          ["confirmation_runs", "Candidate confirmation runs"],
          ["confirmation_reference_runs", "Reference confirmation runs"],
        ] as const
      ).map(
        ([key, title]) =>
          !duplicate &&
          experiment[key] !== undefined && (
            <details key={key} className="disclosure">
              <summary>{title}</summary>
              <RunRecords campaignId={campaignId} value={experiment[key]} />
            </details>
          ),
      )}
      <Reproduction
        campaignId={campaignId}
        experimentId={experiment.id}
        enabled={!duplicate && !priorProtocol && !!experiment.revision}
      />
      <details className="disclosure">
        <summary>Full experiment record · artifacts & reproduction</summary>
        <JsonView value={experiment} />
      </details>
    </section>
  );
}

function Diagnostics({
  detail,
  busy,
  onAction,
}: {
  detail: CampaignDetail;
  busy: string | null;
  onAction: (action: Action) => void;
}) {
  const campaign = detail.campaign;
  const command = detail.protocol?.diagnostic_command;
  const configured = Array.isArray(command) && command.length > 0;
  const approved = Boolean(
    detail.protocol_hash &&
      (campaign.approved_protocol_hash ?? campaign.approved_hash) ===
        detail.protocol_hash,
  );
  const scheduler = isObject(campaign.scheduler) ? campaign.scheduler : {};
  const active = Boolean(
    campaign.active_started_at ||
      Number(scheduler.active_tasks ?? 0) > 0 ||
      [
        "running", "preparing", "assessing", "baselining", "baseline",
        "exploring", "confirming", "stopping",
      ].includes(campaign.state),
  );
  const limits = isObject(campaign.limits) ? campaign.limits : {};
  const durationExhausted =
    typeof limits.max_active_seconds === "number" &&
    Number(campaign.active_seconds ?? 0) >= limits.max_active_seconds;
  const blocked = Boolean(
    campaign.budget_incident ||
      campaign.state === "blocked" ||
      campaign.termination_reason === "budget_exhausted" ||
      durationExhausted,
  );
  const reason = !configured
    ? "Add a diagnostic command to the protocol, then review and approve it."
    : !approved
      ? "Approve this exact protocol before running its diagnostic command."
      : campaign.recovery_required
        ? "Resolve campaign recovery before running a diagnostic."
        : blocked
          ? "Resolve the campaign block or exhausted limit before running a diagnostic."
          : active
            ? "Wait for the current task to finish before running a diagnostic."
            : null;
  const records = Array.isArray(campaign.diagnostics)
    ? campaign.diagnostics.filter(isObject)
    : [];
  return (
    <section aria-labelledby="diagnostics-heading">
      <div className="section-heading">
        <div>
          <h2 id="diagnostics-heading">Diagnostics</h2>
          <p>
            Inspect the workload with the approved diagnostic command.
            Diagnostic results are excluded from performance comparisons.
          </p>
        </div>
      </div>
      {configured && (
        <details className="disclosure">
          <summary>Diagnostic command</summary>
          <JsonView value={command} />
        </details>
      )}
      <div className="inline-actions">
        <button
          className="button"
          disabled={!!busy || !!reason}
          aria-describedby={reason ? "diagnostic-eligibility" : undefined}
          onClick={() => onAction("profile")}
        >
          {busy === "profile" ? "Requesting diagnostic…" : "Run diagnostic"}
        </button>
      </div>
      {reason && (
        <p id="diagnostic-eligibility" className="field-help">{reason}</p>
      )}
      {!records.length && <p className="muted">No diagnostics recorded yet.</p>}
      <div className="run-records">
        {records.map((record, index) => {
          const status = String(record.status ?? "unknown");
          const warning = typeof record.cleanup_warning === "string"
            ? record.cleanup_warning : null;
          const path =
            typeof record.artifact === "string" &&
            record.artifact.startsWith("artifacts/")
              ? record.artifact.slice(10).split("/") : [];
          const safeArtifact = path.length > 0 && path.every(
            (part) => /^[a-zA-Z0-9_-][a-zA-Z0-9_.-]*$/.test(part),
          );
          const statusTone = warning ? "warning"
            : ["failed", "error", "timeout", "timed_out"].includes(status)
              ? "negative" : "neutral";
          return (
            <details className="run-record" key={String(record.label ?? index)}>
              <summary>
                <span>Diagnostic {index + 1}</span>
                <span className={`status ${statusTone}`}>
                  {warning ? "Cleanup warning" : label(status)}
                </span>
                <span>Excluded from comparison</span>
                <code>{String(record.label ?? "Unlabeled diagnostic")}</code>
              </summary>
              {warning && (
                <p role="status">
                  Cleanup warning: {warning}. Recorded execution status: {label(status)}.
                </p>
              )}
              {(["stdout", "stderr"] as const).map((stream) => {
                const output = typeof record[stream] === "string"
                  ? record[stream] : "";
                const limit = stream === "stdout" ? 8000 : 2000;
                return (
                  <details className="disclosure" key={stream}>
                    <summary>
                      {stream} (up to {limit.toLocaleString("en-US")} characters)
                    </summary>
                    <pre className="report-text">
                      {output.slice(0, limit) || "No output recorded."}
                    </pre>
                    <p className="field-help">
                      This preview may be truncated. Download the raw artifact
                      for the full output.
                    </p>
                  </details>
                );
              })}
              {safeArtifact && (
                <div className="artifact-links">
                  {["execution.json", "stdout.log", "stderr.log"].map((file) => (
                    <a
                      key={file}
                      href={`/api/campaigns/${encodeURIComponent(campaign.id)}/artifacts/${path.map(encodeURIComponent).join("/")}/${file}`}
                      download
                    >
                      {file} ↓
                    </a>
                  ))}
                </div>
              )}
            </details>
          );
        })}
      </div>
    </section>
  );
}

function RunRecords({
  campaignId,
  value,
}: {
  campaignId: string;
  value: unknown;
}) {
  if (!Array.isArray(value)) return <EvidenceList value={value} />;
  return (
    <div className="run-records">
      {value.map((sample: unknown, index) => {
        if (!isObject(sample)) return <JsonView key={index} value={sample} />;
        const execution = isObject(sample.execution) ? sample.execution : {};
        const artifact =
          typeof execution.artifact === "string" &&
          execution.artifact.startsWith("artifacts/")
            ? execution.artifact.slice(10)
            : null;
        return (
          <details
            key={String(execution.label ?? index)}
            className="run-record"
          >
            <summary>
              <span>Run {index + 1}</span>
              <code>{String(execution.label ?? "Imported sample")}</code>
              {typeof execution.duration_seconds === "number" && (
                <small>{execution.duration_seconds.toFixed(2)}s</small>
              )}
            </summary>
            <MetricTable value={sample.metrics} />
            <div className="run-checks">
              {isObject(sample.checks) &&
                Object.entries(sample.checks).map(([check, passed]) => (
                  <span key={check}>
                    <Status value={passed === true ? "passed" : "failed"} />{" "}
                    {check}
                  </span>
                ))}
            </div>
            {artifact && (
              <div className="artifact-links">
                {["execution.json", "stdout.log", "stderr.log"].map(
                  (filename) => (
                    <a
                      key={filename}
                      href={`/api/campaigns/${encodeURIComponent(campaignId)}/artifacts/${artifact.split("/").map(encodeURIComponent).join("/")}/${filename}`}
                      download
                    >
                      {filename} ↓
                    </a>
                  ),
                )}
              </div>
            )}
            <details className="disclosure">
              <summary>Raw run evidence & environment</summary>
              <JsonView value={sample} />
            </details>
          </details>
        );
      })}
    </div>
  );
}

function Reproduction({
  campaignId,
  experimentId,
  enabled,
}: {
  campaignId: string;
  experimentId: string;
  enabled: boolean;
}) {
  const [result, setResult] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function load() {
    setBusy(true);
    setError(null);
    try {
      setResult(
        await request(
          `/campaigns/${encodeURIComponent(campaignId)}/reproduce?experiment_id=${encodeURIComponent(experimentId)}`,
        ),
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="reproduction">
      <button className="button" disabled={!enabled || busy} onClick={load}>
        {busy ? "Loading…" : "View reproduction instructions"}
      </button>
      {!enabled && (
        <p className="field-help">
          Reproduction becomes available when an executable revision is
          recorded.
        </p>
      )}
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {result !== null && <JsonView value={result} />}
    </div>
  );
}

function Baseline({ value }: { value: unknown }) {
  if (!isObject(value) || !isObject(value.metrics)) return null;
  return (
    <section className="baseline-summary">
      <div className="baseline-heading">
        <h3>Reference baseline</h3>
        <Status value={value.valid ? "passed" : "invalid"} />
        <span className="muted small">
          {String(value.count ?? "Unknown")} independent runs
        </span>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Metric</th>
              <th>Mean</th>
              <th>95% mean interval</th>
              <th>Variation (CV)</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(value.metrics).map(([name, metric]) => (
              <tr key={name}>
                <td className="mono">{name}</td>
                <td className="mono">
                  {isObject(metric)
                    ? `${numeric(metric.mean)} ${String(metric.unit ?? "")}`
                    : "Not recorded"}
                </td>
                <td className="mono">
                  {isObject(metric) ? interval(metric.ci95) : "Not recorded"}
                </td>
                <td className="mono">
                  {isObject(metric) && typeof metric.cv === "number"
                    ? `${(metric.cv * 100).toFixed(2)}%`
                    : "Not recorded"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="field-help">{display(value.method)}</p>
      {typeof value.measurement_context_warning === "string" && (
        <p className="historical-notice">{value.measurement_context_warning}</p>
      )}
      {Array.isArray(value.reasons) && value.reasons.length > 0 && (
        <p className="form-error">{value.reasons.map(String).join("; ")}</p>
      )}
    </section>
  );
}

function Comparison({ detail }: { detail: CampaignDetail }) {
  const [metric, setMetric] = useState("");
  const [ceiling, setCeiling] = useState("");
  const comparison = detail.comparison;
  const rows = Array.isArray(comparison)
    ? comparison
    : isObject(comparison)
      ? ([
          comparison.candidates,
          comparison.results,
          comparison.experiments,
          comparison.rows,
        ].find(Array.isArray) as unknown[] | undefined)
      : undefined;
  const allRows = (rows ?? [])
    .filter(isObject)
    .filter((row) => row.termination_reason !== "duplicate_program");
  const objects = allRows.filter(
    (row) =>
      row.protocol_hash === detail.protocol_hash && !!detail.protocol_hash,
  );
  const priorCount = detail.experiments.filter(
    (experiment) => experiment.protocol_hash !== detail.protocol_hash,
  ).length;
  const measuring = detail.experiments.filter(
    (experiment) =>
      !!detail.protocol_hash &&
      experiment.protocol_hash === detail.protocol_hash &&
      experiment.status === "running" &&
      ["measuring", "confirming"].includes(String(experiment.phase)),
  );
  const confirming = measuring.some(
    (experiment) => experiment.phase === "confirming",
  );
  const baseline =
    isObject(detail.campaign.baseline_summary) &&
    detail.campaign.baseline_summary.protocol_hash === detail.protocol_hash
      ? detail.campaign.baseline_summary
      : null;
  const metricNames = [
    ...new Set(objects.flatMap((row) => Object.keys(comparisonMetrics(row)))),
  ];
  const metricSpecs =
    detail.protocol && Array.isArray(detail.protocol.metrics)
      ? detail.protocol.metrics.filter(isObject)
      : [];
  const filtered = objects.filter((row) => {
    if (!metric || ceiling === "") return true;
    const value = candidateValue(comparisonMetrics(row)[metric]);
    return value !== undefined && value <= Number(ceiling);
  });
  return (
    <>
      <div className="section-heading">
        <div>
          <div className="eyebrow">MEASUREMENT & TRADEOFFS</div>
          <h2>Results comparison</h2>
          <p>
            Baseline and candidate values, hard constraints, and measurement
            uncertainty.
          </p>
        </div>
      </div>
      <Baseline value={baseline} />
      {priorCount > 0 && (
        <p className="historical-notice">
          {priorCount} prior-protocol attempt
          {priorCount === 1 ? " is" : "s are"} retained in Experiments. Current
          comparison includes only this protocol version.
        </p>
      )}
      {objects.length ? (
        <>
          <div className="table-toolbar">
            <label className="comparison-filter">
              Metric ceiling
              <select
                value={metric}
                onChange={(e) => setMetric(e.target.value)}
              >
                <option value="">Choose a measured metric</option>
                {metricNames.map((name) => (
                  <option key={name}>{name}</option>
                ))}
              </select>
            </label>
            <label className="comparison-filter">
              Maximum value
              <input
                type="number"
                step="any"
                value={ceiling}
                onChange={(e) => setCeiling(e.target.value)}
                placeholder="No maximum"
                disabled={!metric}
              />
            </label>
            <button
              className="button quiet small"
              onClick={() => {
                setMetric("");
                setCeiling("");
              }}
            >
              Clear filter
            </button>
          </div>
          <div className="table-scroll">
            <table className="comparison-table">
              <thead>
                <tr>
                  <th>Candidate</th>
                  <th>Evidence status</th>
                  {metricNames.map((name) => (
                    <th key={name}>
                      {name}
                      <span className="metric-unit">
                        {String(
                          metricSpecs.find((spec) => spec.name === name)
                            ?.unit ?? "",
                        )}{" "}
                        ·{" "}
                        {String(
                          metricSpecs.find((spec) => spec.name === name)
                            ?.role ?? "metric",
                        )}
                        <br />
                        baseline → candidate
                      </span>
                    </th>
                  ))}
                  <th>Decision / uncertainty</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((row, index) => (
                  <tr key={String(row.id ?? index)}>
                    <td>
                      <strong>
                        {String(
                          row.hypothesis ??
                            row.name ??
                            `Candidate ${index + 1}`,
                        )}
                      </strong>
                      <details className="comparison-record">
                        <summary>Record</summary>
                        <code>
                          {String(
                            row.id ?? row.experiment_id ?? "Not recorded",
                          )}
                        </code>
                      </details>
                    </td>
                    <td>
                      <Status
                        value={String(
                          row.verdict ??
                            row.status ??
                            row.decision ??
                            "not recorded",
                        )}
                      />
                    </td>
                    {metricNames.map((name) => (
                      <td key={name} className="mono">
                        <ComparisonValue value={comparisonMetrics(row)[name]} />
                      </td>
                    ))}
                    <td>
                      <ComparisonReason row={row} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!filtered.length && (
              <Empty
                title="No candidate meets this ceiling."
                text="Adjust the maximum or clear the filter to review all candidates."
              />
            )}
          </div>
        </>
      ) : (
        <Empty
          title={
            confirming
              ? "Confirmation in progress"
              : measuring.length
                ? "Comparison in progress"
                : "No candidate measurements yet"
          }
          text={
            measuring.length
              ? `Review individual executions in Activity. The ${confirming ? "confirmation" : "comparison"} summary appears after the required repetitions finish.`
              : "Establish a repeated baseline and evaluate a candidate under the same approved protocol. Exploratory results are not confirmed improvements."
          }
        />
      )}
      {comparison !== null && comparison !== undefined && (
        <details className="disclosure" open={!objects.length}>
          <summary>Full comparison evidence & measurement context</summary>
          <JsonView value={comparison} />
        </details>
      )}
      <div className="comparison-principles">
        <div>
          <strong>Exploratory</strong>
          <p>A useful signal that still needs independent confirmation.</p>
        </div>
        <div>
          <strong>Confirmed</strong>
          <p>A result established by the engine's confirmation protocol.</p>
        </div>
        <div>
          <strong>Inconclusive</strong>
          <p>The available measurements do not resolve the difference.</p>
        </div>
      </div>
    </>
  );
}

function ComparisonValue({ value }: { value: unknown }) {
  if (!isObject(value)) return <>{display(value)}</>;
  const gain = value.improvement_fraction;
  return (
    <>
      <span className="baseline-value">{numeric(value.baseline)} → </span>
      <strong>{numeric(value.candidate ?? value.mean)}</strong>
      {typeof gain === "number" && Number.isFinite(gain) && (
        <span className="metric-gain">
          {gain >= 0 ? "+" : ""}
          {(gain * 100).toLocaleString("en-US", { maximumFractionDigits: 4 })}%
          observed improvement
        </span>
      )}
      {value.resolved_improvement === false && (
        <span className="metric-gain unresolved">Improvement unresolved</span>
      )}
      {value.resolved_regression === true && (
        <span className="metric-gain regression">Resolved regression</span>
      )}
      {value.gain_ci95 !== undefined && (
        <span className="metric-gain">
          Gain 95% interval: {interval(value.gain_ci95)}
        </span>
      )}
    </>
  );
}

function ComparisonReason({ row }: { row: JsonObject }) {
  const result = isObject(row.confirmation)
    ? row.confirmation
    : isObject(row.comparison)
      ? row.comparison
      : row;
  const reasons = result.reasons;
  return (
    <>
      {typeof row.measurement_context_warning === "string" && (
        <p className="measurement-context-warning">
          {row.measurement_context_warning}
        </p>
      )}
      {Array.isArray(reasons) && reasons.length
        ? reasons.map(String).join("; ")
        : display(
            row.reason ??
              row.uncertainty ??
              row.conclusion ??
              (row.verdict === "confirmed"
                ? "Independent confirmation recorded."
                : "Review measurement intervals."),
          )}
      <details className="comparison-evidence">
        <summary>Full measurement evidence</summary>
        <JsonView value={result} />
      </details>
    </>
  );
}

function interval(value: unknown) {
  return Array.isArray(value) && value.length === 2
    ? `${numeric(value[0])} to ${numeric(value[1])}`
    : display(value);
}

function numeric(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("en-US", { maximumSignificantDigits: 7 })
    : "Not recorded";
}

function Activity({ events }: { events: ResearchEvent[] }) {
  const [filter, setFilter] = useState("");
  const filtered = events.filter((event) =>
    `${event.type} ${JSON.stringify(event.data)}`
      .toLowerCase()
      .includes(filter.toLowerCase()),
  );
  return (
    <>
      <div className="section-heading">
        <div>
          <div className="eyebrow">DURABLE EVENT LOG</div>
          <h2>Activity</h2>
          <p>The supervisor’s durable record of decisions and executions.</p>
        </div>
        <label className="search-field">
          <span className="sr-only">Search events</span>
          <input
            placeholder="Filter event log…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </label>
      </div>
      {events.length ? (
        <ol className="event-log">
          {[...filtered].reverse().map((event) => (
            <li key={event.seq}>
              <div className="event-index mono">
                {String(event.seq).padStart(3, "0")}
              </div>
              <div className="event-content">
                <div>
                  <strong>{label(event.type)}</strong>
                  <time dateTime={event.created_at}>
                    {formatTime(event.created_at)}
                  </time>
                </div>
                <details>
                  <summary>View event data</summary>
                  <JsonView value={event.data} />
                </details>
              </div>
            </li>
          ))}
        </ol>
      ) : (
        <Empty
          title="No events have been recorded."
          text="The supervisor will record each lifecycle transition and its evidence here."
        />
      )}
      {events.length > 0 && !filtered.length && (
        <p className="muted">No events match this filter.</p>
      )}
    </>
  );
}

function Report({ detail }: { detail: CampaignDetail }) {
  const [report, setReport] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  async function load() {
    setLoading(true);
    setError(null);
    try {
      setReport(
        await request(
          `/campaigns/${encodeURIComponent(detail.campaign.id)}/report`,
          {},
          "text",
        ),
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }
  return (
    <>
      <div className="section-heading">
        <div>
          <div className="eyebrow">REVIEW & REPRODUCTION</div>
          <h2>Report & export</h2>
          <p>
            Review the conclusion and export the evidence needed to reproduce
            it.
          </p>
        </div>
      </div>
      <div className="report-actions">
        <div>
          <Icon className="report-icon" name="external" size={20} />
          <h3>Campaign report</h3>
          <p>
            A report from the retained evidence, available without another model
            call.
          </p>
          <button className="button primary" onClick={load} disabled={loading}>
            {loading ? "Loading report…" : "View current report"}
          </button>
        </div>
        <div>
          <Icon className="report-icon" name="download" size={20} />
          <h3>Portable evidence</h3>
          <p>
            Export the complete campaign record. Missing artifacts remain
            explicitly documented.
          </p>
          <a
            className="button"
            href={`/api/campaigns/${encodeURIComponent(detail.campaign.id)}/export`}
            download
          >
            Download export
          </a>
        </div>
      </div>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      {report !== null && (
        <section className="report-output">
          <h3>Current report</h3>
          {typeof report === "string" ? (
            <pre className="report-text">{report}</pre>
          ) : (
            <JsonView value={report} />
          )}
        </section>
      )}
      <Publication detail={detail} />
      <div className="review-note">
        <strong>Publication is an explicit step.</strong>
        <p>
          Review the exact patch, measurements, limits, and reproduction
          commands before sharing a candidate. Draft PR availability depends on
          the configured publication connector.
        </p>
      </div>
    </>
  );
}

function Publication({ detail }: { detail: CampaignDetail }) {
  const candidates = detail.experiments.filter(
    (experiment) =>
      experiment.verdict === "confirmed" &&
      experiment.termination_reason !== "duplicate_program" &&
      experiment.protocol_hash === detail.protocol_hash,
  );
  const [candidateId, setCandidateId] = useState("");
  const [draft, setDraft] = useState<JsonObject | null>(null);
  const [draftProtocolHash, setDraftProtocolHash] = useState<string | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [published, setPublished] = useState<JsonObject | null>(null);
  const [reviewed, setReviewed] = useState(false);
  const selectedCandidateIsCurrent = candidates.some(
    (candidate) => candidate.id === candidateId,
  );
  const draftIsCurrent =
    selectedCandidateIsCurrent && draftProtocolHash === detail.protocol_hash;
  async function review() {
    if (!selectedCandidateIsCurrent) return;
    const protocolHash = detail.protocol_hash;
    setBusy(true);
    setError(null);
    setReviewed(false);
    setDraft(null);
    try {
      setDraft(
        await request<JsonObject>(
          `/campaigns/${encodeURIComponent(detail.campaign.id)}/draft?experiment_id=${encodeURIComponent(candidateId)}`,
        ),
      );
      setDraftProtocolHash(protocolHash);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function publish(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft || !reviewed || !draftIsCurrent) return;
    const fields = new FormData(event.currentTarget);
    setBusy(true);
    setError(null);
    try {
      setPublished(
        await request<JsonObject>(
          `/campaigns/${encodeURIComponent(detail.campaign.id)}/publish`,
          {
            method: "POST",
            body: JSON.stringify({
              candidate_id: draft.candidate_id,
              review_hash: draft.review_hash,
              remote_repository: String(fields.get("remote_repository")).trim(),
              base_branch: String(fields.get("base_branch")).trim(),
              head_branch: String(fields.get("head_branch")).trim(),
            }),
          },
        ),
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="publication">
      <div className="section-heading">
        <div>
          <div className="eyebrow">CANDIDATE REVIEW</div>
          <h2>Draft pull request</h2>
          <p>Only independently confirmed candidates can be published.</p>
        </div>
      </div>
      {candidates.length ? (
        <>
          <div className="inline-actions">
            <label className="candidate-select">
              <span className="sr-only">Confirmed candidate</span>
              <select
                value={candidateId}
                onChange={(e) => {
                  setCandidateId(e.target.value);
                  setDraft(null);
                  setReviewed(false);
                  setPublished(null);
                }}
              >
                <option value="">Select a confirmed candidate</option>
                {candidates.map((candidate) => (
                  <option key={candidate.id} value={candidate.id}>
                    {candidate.id.slice(0, 12)} · {candidate.hypothesis}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="button"
              disabled={!selectedCandidateIsCurrent || busy}
              onClick={review}
            >
              {busy ? "Loading…" : "Review draft content"}
            </button>
          </div>
          {draft && (
            <form className="draft-review" onSubmit={publish}>
              {!draftIsCurrent && (
                <p className="form-error">
                  The measurement context changed. Select a current confirmed
                  candidate and review its draft again.
                </p>
              )}
              <h3>{String(draft.title ?? "Draft pull request")}</h3>
              <pre className="report-text">{display(draft.body)}</pre>
              <details className="disclosure">
                <summary>Publication review record</summary>
                <JsonView value={draft} />
              </details>
              <div className="field">
                <label htmlFor="remote_repository">
                  Destination repository
                </label>
                <input
                  id="remote_repository"
                  name="remote_repository"
                  placeholder="owner/repository"
                  required
                />
              </div>
              <div className="two-fields">
                <div className="field">
                  <label htmlFor="base_branch">Base branch</label>
                  <input
                    id="base_branch"
                    name="base_branch"
                    placeholder="main"
                    required
                  />
                </div>
                <div className="field">
                  <label htmlFor="head_branch">New candidate branch</label>
                  <input
                    id="head_branch"
                    name="head_branch"
                    placeholder="autoresearch/confirmed-candidate"
                    required
                  />
                  <span className="field-help">
                    Use the autoresearch/ prefix for a new candidate branch.
                  </span>
                </div>
              </div>
              <label className="check-label">
                <input
                  type="checkbox"
                  checked={reviewed}
                  onChange={(e) => setReviewed(e.target.checked)}
                  required
                />
                <span>
                  I reviewed this candidate's patch, evidence, limitations, and
                  publication destination.
                </span>
              </label>
              <button
                className="button primary"
                disabled={busy || !reviewed || !draftIsCurrent || !!published}
              >
                {busy
                  ? "Publishing…"
                  : published
                    ? "Draft PR created"
                    : "Create draft PR"}
              </button>
            </form>
          )}
          {published && (
            <div className="message notice" role="status">
              <span>Publication request completed.</span>
              {typeof published.url === "string" &&
              /^https:\/\//.test(published.url) ? (
                <a href={published.url} target="_blank" rel="noreferrer">
                  Open draft PR ↗
                </a>
              ) : (
                <JsonView value={published} />
              )}
            </div>
          )}
        </>
      ) : (
        <p className="muted small">
          No confirmed candidate is available yet. A report and export remain
          available for every outcome.
        </p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

function JsonView({ value }: { value: unknown }) {
  return <pre className="json-view">{display(value)}</pre>;
}
function Empty({ title, text }: { title: string; text: string }) {
  return (
    <div className="empty-state">
      <Icon name="flask" size={20} className="empty-glyph" />
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  );
}
function EvidenceList({ value }: { value: unknown }) {
  if (value === undefined || value === null)
    return <p className="muted">No observations recorded yet.</p>;
  if (Array.isArray(value))
    return (
      <div className="evidence-list">
        {value.map((item, index) => (
          <div className="evidence-item" key={index}>
            {isObject(item) ? (
              <>
                <div className="evidence-item-heading">
                  <strong>
                    {display(
                      item.title ??
                        item.name ??
                        item.message ??
                        item.check ??
                        item.observation ??
                        `Evidence ${index + 1}`,
                    )}
                  </strong>
                  {typeof item.status === "string" && (
                    <Status value={item.status} />
                  )}
                </div>
                <dl className="compact-definition">
                  {Object.entries(item)
                    .filter(
                      ([key]) =>
                        !["title", "name", "message", "status"].includes(key),
                    )
                    .map(([key, entry]) => (
                      <div key={key}>
                        <dt>{label(key)}</dt>
                        <dd>
                          {typeof entry === "object" ? (
                            <JsonView value={entry} />
                          ) : (
                            display(entry)
                          )}
                        </dd>
                      </div>
                    ))}
                </dl>
              </>
            ) : (
              <p>{display(item)}</p>
            )}
          </div>
        ))}
      </div>
    );
  if (isObject(value)) return <JsonView value={value} />;
  return <p className="evidence-text">{display(value)}</p>;
}
function MetricTable({ value }: { value: unknown }) {
  if (!isObject(value)) return <JsonView value={value} />;
  return (
    <dl className="metric-values">
      {Object.entries(value).map(([key, entry]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd className="mono">
            {isObject(entry) &&
            ("baseline" in entry || "candidate" in entry) ? (
              <ComparisonValue value={entry} />
            ) : typeof entry === "number" && Number.isFinite(entry) ? (
              entry.toLocaleString("en-US", { maximumSignificantDigits: 12 })
            ) : (
              display(entry)
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}
function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
