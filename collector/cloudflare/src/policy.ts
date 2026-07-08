import type { AssetResult } from "./types";

export const INCIDENT_FAILURE_THRESHOLD = 3;

export function shouldOpenIncident(consecutiveFailures: number): boolean {
  return consecutiveFailures >= INCIDENT_FAILURE_THRESHOLD;
}

export function shouldResolveIncident(result: AssetResult): boolean {
  return result.status === "ok";
}
