export type JsonObject = Record<string, unknown>;

export interface Campaign extends JsonObject {
  id: string;
  name: string;
  goal: string;
  repository: string;
  state: string;
  outcome?: string | null;
  termination_reason?: string | null;
  created_at?: string;
}

export interface Budget extends JsonObject {
  limit: number;
  consumed: number;
  engaged: number;
  protected: number;
  available: number;
  currency?: string;
  enforcement?: string;
  scope?: unknown;
}

export interface Experiment extends JsonObject {
  id: string;
  hypothesis: string;
  strategy?: string;
  status?: string;
  state?: string;
  parent_id?: string | null;
  patch?: string;
}

export interface ResearchEvent extends JsonObject {
  seq: number;
  type: string;
  created_at: string;
  data: unknown;
}

export interface CampaignDetail {
  campaign: Campaign;
  protocol: JsonObject | null;
  protocol_hash: string | null;
  readiness: JsonObject | null;
  budget: Budget;
  experiments: Experiment[];
  events: ResearchEvent[];
  comparison: unknown;
  insights?: JsonObject | null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function request<T>(
  path: string,
  options: RequestInit = {},
  format: "json" | "text" = "json",
): Promise<T> {
  let response: Response;
  const mutation =
    options.method &&
    !["GET", "HEAD", "OPTIONS"].includes(options.method.toUpperCase());
  try {
    response = await fetch(`/api${path}`, {
      ...options,
      headers: {
        Accept: format === "text" ? "text/plain" : "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(mutation ? { "Idempotency-Key": crypto.randomUUID() } : {}),
        ...options.headers,
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new ApiError(
      0,
      "Cannot reach the supervisor. Check that the API is running, then retry.",
    );
  }
  const contentType = response.headers.get("content-type") ?? "";
  const body: unknown = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const detail = isObject(body) ? body.detail : body;
    throw new ApiError(
      response.status,
      typeof detail === "string"
        ? detail
        : JSON.stringify(detail ?? `Request failed (${response.status})`),
    );
  }
  if (format === "json" && !contentType.includes("application/json"))
    throw new ApiError(
      response.status,
      "The supervisor returned an unexpected response. Check the API address and server logs.",
    );
  return body as T;
}

export function isObject(value: unknown): value is JsonObject {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function campaignList(
  value: Campaign[] | { campaigns: Campaign[] },
): Campaign[] {
  const list = Array.isArray(value) ? value : value.campaigns;
  if (!Array.isArray(list))
    throw new ApiError(
      200,
      "The supervisor returned an invalid campaign list.",
    );
  return list;
}

export function money(microUsd: number | undefined): string {
  if (typeof microUsd !== "number" || !Number.isFinite(microUsd)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 6,
  }).format(microUsd / 1_000_000);
}

export function label(value: string | null | undefined): string {
  return value ? value.replaceAll("_", " ") : "Not available";
}

export function display(value: unknown): string {
  if (value === null || value === undefined) return "Not recorded";
  return typeof value === "object"
    ? JSON.stringify(value, null, 2)
    : String(value);
}

export function controlPolicy(
  state: string,
  approved: boolean,
  reconciled = false,
) {
  return {
    run:
      approved &&
      !(state === "blocked" && reconciled) &&
      [
        "approved",
        "awaiting_approval",
        "ready",
        "created",
        "idle",
        "draft",
        "reviewable",
        "blocked",
      ].includes(state),
    pause: [
      "running",
      "baseline",
      "baselining",
      "exploring",
      "confirming",
      "preparing",
    ].includes(state),
    stop: !["stopped", "completed", "failed", "cancelled"].includes(state),
    resume:
      ["paused", "stopped", "interrupted"].includes(state) ||
      (state === "blocked" && reconciled),
  };
}

export function comparisonMetrics(experiment: JsonObject): JsonObject {
  if (experiment.termination_reason === "duplicate_program") return {};
  const result = isObject(experiment.confirmation)
    ? experiment.confirmation
    : isObject(experiment.comparison)
      ? experiment.comparison
      : experiment;
  return isObject(result.metrics) ? result.metrics : {};
}

export function candidateValue(metric: unknown): number | undefined {
  const value = isObject(metric) ? (metric.candidate ?? metric.mean) : metric;
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}
