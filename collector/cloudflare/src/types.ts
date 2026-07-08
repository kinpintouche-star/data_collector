export interface Env {
  DATABASE_URL: string;
  COLLECTOR_TOKEN: string;
}

export type TriggerType = "scheduled" | "manual";
export type CollectorStatus = "ok" | "warning" | "error" | "stale" | "pending";
export type LiveProvider = "binance" | "coinbase" | "kraken";

export interface LiveSource {
  symbolCode: string;
  sourceName: string;
  provider: LiveProvider;
  sourceSymbol: string;
  providerSymbol?: string;
  fallbackProvider?: LiveProvider;
  fallbackProviderSymbol?: string;
  timeframe: "M1";
  pollIntervalMinutes: number;
  retentionDays: number;
  enabled: boolean;
  priority: number;
  collectionMode: "daily";
  overlapMinutes: number;
}

export interface Candle {
  symbolCode: string;
  sourceName: string;
  sourceSymbol: string;
  timeframe: string;
  timeOpen: string;
  open: string;
  high: string;
  low: string;
  close: string;
  tickVolume: number | null;
  realVolume: number | null;
  spread: string | null;
  qualityFlags: Record<string, unknown>;
  metadata: Record<string, unknown>;
}

export interface AssetResult {
  asset: LiveSource;
  status: CollectorStatus;
  rowsFetched: number;
  rowsWritten: number;
  lastCandleTime: string | null;
  errorMessage?: string;
}

export interface CollectorRunSummary {
  runId: string;
  status: "completed" | "failed";
  assetsRequested: number;
  assetsSucceeded: number;
  assetsFailed: number;
  rowsFetched: number;
  rowsWritten: number;
  errorMessage?: string;
  results: AssetResult[];
}
