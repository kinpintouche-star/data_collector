import type {
  BacktestJob,
  BacktestLaunchPayload,
  BacktestOptions,
  DataApiUsagePayload,
  DataCoveragePayload,
  DataFetchJob,
  DataFetchPayload,
  RunAnalytics,
  RunGroupSummary,
  RunSummary,
  StrategyBuilderCatalog,
  StrategyDefinition,
  StrategyDefinitionPayload,
  StrategyValidationResult,
  TradeReview,
  TradeSummary
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

async function requestJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function postJson<T>(path: string, payload: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function getRuns(): Promise<RunSummary[]> {
  return requestJson<RunSummary[]>("/api/runs");
}

export function getRunGroups(): Promise<RunGroupSummary[]> {
  return requestJson<RunGroupSummary[]>("/api/run-groups");
}

export function getBacktestOptions(): Promise<BacktestOptions> {
  return requestJson<BacktestOptions>("/api/backtest/options");
}

export function launchBacktest(payload: BacktestLaunchPayload): Promise<BacktestJob> {
  return postJson<BacktestJob>("/api/backtest/jobs", payload);
}

export function getBacktestJob(jobId: string): Promise<BacktestJob> {
  return requestJson<BacktestJob>(`/api/backtest/jobs/${jobId}`);
}

export function getDataCoverage(): Promise<DataCoveragePayload> {
  return requestJson<DataCoveragePayload>("/api/data/coverage");
}

export function getDataApiUsage(): Promise<DataApiUsagePayload> {
  return requestJson<DataApiUsagePayload>("/api/data/api-usage");
}

export function launchDataFetch(payload: DataFetchPayload): Promise<DataFetchJob> {
  return postJson<DataFetchJob>("/api/data/fetch-jobs", payload);
}

export function getDataFetchJob(jobId: string): Promise<DataFetchJob> {
  return requestJson<DataFetchJob>(`/api/data/fetch-jobs/${jobId}`);
}

export function getRunTrades(runId: string): Promise<TradeSummary[]> {
  return requestJson<TradeSummary[]>(`/api/runs/${runId}/trades`);
}

export function getRunAnalytics(runId: string): Promise<RunAnalytics> {
  return requestJson<RunAnalytics>(`/api/runs/${runId}/analytics`);
}

export function getRunGroupAnalytics(groupId: string, symbols: string[]): Promise<RunAnalytics> {
  const params = symbols.length ? `?symbols=${encodeURIComponent(symbols.join(","))}` : "";
  return requestJson<RunAnalytics>(`/api/run-groups/${groupId}/analytics${params}`);
}

export function getTradeReview(tradeId: string): Promise<TradeReview> {
  return requestJson<TradeReview>(`/api/trades/${tradeId}/review`);
}

export function getStrategyBuilderCatalog(): Promise<StrategyBuilderCatalog> {
  return requestJson<StrategyBuilderCatalog>("/api/strategy-builder/catalog");
}

export function getStrategyDefinitions(): Promise<StrategyDefinition[]> {
  return requestJson<StrategyDefinition[]>("/api/strategy-builder/strategies");
}

export function createStrategyDefinition(payload: {
  name: string;
  version: string;
  description?: string;
  template_id?: string;
  definition?: StrategyDefinitionPayload;
}): Promise<StrategyDefinition> {
  return postJson<StrategyDefinition>("/api/strategy-builder/strategies", payload);
}

export function updateStrategyDefinition(
  strategyId: string,
  payload: {
    name?: string;
    version?: string;
    description?: string;
    definition?: StrategyDefinitionPayload;
  }
): Promise<StrategyDefinition> {
  return fetchJsonWithMethod<StrategyDefinition>(`/api/strategy-builder/strategies/${strategyId}`, "PUT", payload);
}

export function validateStrategyDefinition(strategyId: string): Promise<StrategyValidationResult> {
  return postJson<StrategyValidationResult>(`/api/strategy-builder/strategies/${strategyId}/validate`, {});
}

export function exportStrategyDefinition(strategyId: string): Promise<{ exported_path: string; strategy: StrategyDefinition }> {
  return postJson<{ exported_path: string; strategy: StrategyDefinition }>(`/api/strategy-builder/strategies/${strategyId}/export`, {});
}

export function deleteStrategyDefinition(strategyId: string): Promise<{ deleted_id: string }> {
  return fetchJsonWithMethod<{ deleted_id: string }>(`/api/strategy-builder/strategies/${strategyId}`, "DELETE");
}

async function fetchJsonWithMethod<T>(path: string, method: "PUT" | "DELETE", payload?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: payload === undefined ? undefined : JSON.stringify(payload)
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}
