import { label } from "./api";

export function Status({ value }: { value: string }) {
  return (
    <span className={`status ${tone(value)}`}>
      <span className="status-dot" />
      {label(value)}
    </span>
  );
}
export function tone(value: string): string {
  if (
    [
      "ready",
      "approved",
      "protocol approved",
      "confirmed",
      "supported",
      "completed",
      "strict",
      "passed",
    ].includes(value)
  )
    return "positive";
  if (
    [
      "failed",
      "blocked_external",
      "invalid",
      "rejected",
      "refuted",
      "error",
      "execution failed",
      "constraint failed",
      "constraint_failed",
      "invalid measurement",
    ].includes(value)
  )
    return "negative";
  if (
    [
      "paused",
      "inconclusive",
      "needs_preparation",
      "measurement_unreliable",
      "interrupted",
      "estimated",
    ].includes(value)
  )
    return "warning";
  if (
    [
      "running",
      "preparing",
      "assessing",
      "exploring",
      "baseline",
      "baselining",
      "confirming",
      "queued",
    ].includes(value)
  )
    return "active";
  return "neutral";
}
