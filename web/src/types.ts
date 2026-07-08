export type RunSummary = {
  id: string;
  status: string;
  run_type: string;
  start_time: string;
  end_time: string;
  created_at: string;
  launch_id?: string | null;
  launch_label?: string | null;
  symbol_code: string;
  source_name: string;
  strategy_name: string;
  strategy_version: string;
  parameter_set_name: string;
  timeframe: string;
  total_trades: number;
  winrate: number | null;
  avg_rr: number | null;
  profit_factor: number | null;
  net_profit: number | null;
  max_drawdown_pct: number | null;
};

export type TradeSummary = {
  id: string;
  run_id: string;
  setup_id: string;
  direction: "bullish" | "bearish";
  entry_time: string;
  entry_price: number;
  exit_time: string | null;
  exit_price: number | null;
  volume: number;
  sl: number;
  tp: number;
  exit_reason: string | null;
  pnl: number | null;
  pnl_points: number | null;
  rr: number | null;
  pd_type: string | null;
  strategy_mode: string | null;
  session_name: string | null;
  symbol_code: string;
  source_name: string;
};

export type Candle = {
  time: number;
  time_open: string;
  open: number;
  high: number;
  low: number;
  close: number;
  tick_volume: number;
  real_volume: number;
  spread: number;
};

export type GapSummary = {
  gap_count: number;
  missing_candles: number;
  largest_gap_candles: number;
  gaps: Array<{ after: string; before: string; missing_candles: number }>;
};

export type TimeframePayload = {
  timeframe: TimeframeCode;
  window_start: string;
  window_end: string;
  candles: Candle[];
  gap_summary: GapSummary;
};

export type TimeframeCode = "H4" | "H1" | "M30" | "M15" | "M5" | "M1";

export type TradeEvent = {
  id: string;
  setup_id: string;
  event_type: string;
  event_time: string;
  direction: string | null;
  price: number | null;
  state_before: string | null;
  state_after: string | null;
  metadata: Record<string, unknown>;
};

export type TradeMarker = {
  kind: "entry" | "exit" | "event";
  event_type?: string;
  time: string;
  price: number | null;
  direction: string | null;
  label: string;
};

export type TradeAnnotations = {
  zones: Array<{
    id: string;
    kind: "OB" | "FVG" | "OTE";
    label: string;
    direction: string | null;
    start_time: string | null;
    end_time: string | null;
    bottom: number;
    top: number;
    mid: number | null;
    visible_timeframes: TimeframeCode[];
  }>;
  swings: Array<{
    id: string;
    label: string;
    kind: "swing" | "leg";
    time: string;
    price: number;
    direction: string | null;
    visible_timeframes: TimeframeCode[];
  }>;
  levels: Array<{
    id: string;
    label: string;
    kind: "crt_high" | "crt_mid" | "crt_low" | "target" | "target_candidate";
    price: number;
    start_time: string | null;
    end_time: string | null;
    visible_timeframes: TimeframeCode[];
  }>;
};

export type FibPayload = {
  available: boolean;
  source: string;
  direction: string;
  visible_timeframes: TimeframeCode[];
  anchor_start?: { time: string | null; price: number };
  anchor_end?: { time: string | null; price: number };
  levels: Array<{ level: number; label: string; price: number }>;
  ote_zone: { bottom: number; top: number } | null;
};

export type RiskRewardPayload = {
  entry: number;
  sl: number;
  tp: number;
  exit: number | null;
  risk: number;
  reward: number;
  planned_rr: number | null;
  realized_rr: number | null;
  risk_zone: { bottom: number; top: number };
  reward_zone: { bottom: number; top: number };
} | null;

export type TradeReview = {
  trade: TradeSummary & { metadata?: Record<string, unknown> };
  run: {
    id: string;
    status: string;
    run_type: string;
    strategy_name: string;
    strategy_version: string;
    parameter_set_name: string;
    start_time: string;
    end_time: string;
    created_at: string;
    initial_balance: number | null;
    final_balance: number | null;
  };
  symbol: { id: string; code: string; asset_type: string };
  source: { id: string; name: string; type: string };
  dataset: { id: string; timeframe: string; start_time: string; end_time: string };
  timeframes: Record<TimeframeCode, TimeframePayload>;
  events: TradeEvent[];
  markers: TradeMarker[];
  annotations: TradeAnnotations;
  fib: FibPayload;
  risk_reward: RiskRewardPayload;
  quality: {
    has_gaps: boolean;
    missing_m1_candles_across_windows: number;
    largest_gap_candles: number;
  };
};

export type AnalyticsSummary = {
  total_trades: number;
  wins: number;
  losses: number;
  breakeven: number;
  winrate: number | null;
  loss_rate: number | null;
  net_pnl: number;
  gross_profit: number;
  gross_loss: number;
  profit_factor: number | null;
  avg_rr: number | null;
  median_rr: number | null;
  expectancy: number | null;
  best_trade: number | null;
  worst_trade: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  payoff_ratio: number | null;
  max_consecutive_losses: number;
  avg_duration_minutes: number | null;
};

export type AnalyticsPoint = {
  time: string;
  value?: number | null;
  pnl?: number | null;
  rr?: number | null;
  won?: boolean;
  balance?: number | null;
  equity?: number | null;
  drawdown_abs?: number | null;
  drawdown_pct?: number | null;
  open_positions?: number;
};

export type AnalyticsBreakdownRow = {
  name: string;
  trades: number;
  pnl: number;
  winrate: number | null;
  avg_rr: number | null;
  expectancy: number | null;
  profit_factor: number | null;
};

export type RunGroupSummary = {
  group_id: string;
  label: string;
  status: string;
  start_time: string;
  end_time: string;
  created_at: string;
  run_count: number;
  run_ids: string[];
  symbols: string[];
  sources: string[];
  strategy_name: string;
  strategy_version: string;
  parameter_set_name: string;
  total_trades: number;
  total_wins: number;
  total_losses: number;
  winrate: number | null;
  avg_rr: number | null;
  net_profit: number | null;
  profit_factor: number | null;
};

export type BacktestAssetOption = {
  symbol_code: string;
  asset_type: string;
  source_name: string;
  source_type: string;
  start_time: string;
  end_time: string;
  candles: number;
};

export type BacktestStrategyOption = {
  path: string;
  label: string;
  kind?: "yaml" | "builder";
  status?: string;
  strategy_definition_id?: string;
};

export type BacktestOptions = {
  strategies: BacktestStrategyOption[];
  assets: BacktestAssetOption[];
  defaults: {
    strategy_config: string;
    from: string | null;
    to: string | null;
    timeframe: string;
  };
};

export type BacktestJob = {
  id: string;
  status: "queued" | "running" | "completed" | "failed" | "partial";
  launch_id: string;
  label: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  total_assets: number;
  completed_assets: number;
  failed_assets: number;
  results: Array<Record<string, unknown>>;
  errors: Array<Record<string, unknown>>;
};

export type DataFetchChannel = "auto" | "neon" | "databento";

export type DataCoverageRow = {
  symbol_code: string;
  group: string | null;
  source_name: string;
  source_type: string | null;
  asset_type: string | null;
  recommended_channel: "Neon" | "Databento";
  candle_rows: number;
  first_candle_time: string | null;
  last_candle_time: string | null;
  last_ingested_at: string | null;
  local_last: string | null;
  neon_last: string | null;
  neon_enabled: boolean;
  neon_status: string | null;
  neon_sources: string | null;
  missing_from_neon_min: number | null;
  flagged_candles: number;
  sample_source_symbol: string | null;
  complete_day_ok: boolean;
  today_present: boolean;
  freshness_status: "complete_day_ok" | "stale" | "empty";
  needs_attention: boolean;
};

export type DataCoveragePayload = {
  generated_at: string;
  settings: {
    neon_configured: boolean;
    databento_configured: boolean;
  };
  summary: {
    assets: number;
    asset_sources: number;
    complete_day_ok: number;
    today_present: number;
    empty: number;
    stale: number;
    total_candles: number;
    flagged_candles: number;
  };
  rows: DataCoverageRow[];
};

export type DataApiUsageRow = {
  fetch_channel: string;
  asset_count: number;
  assets: string;
  sources: string;
  usage: string;
  limits: string;
  current_split: string;
  cost: string;
};

export type DataApiUsagePayload = {
  generated_at: string;
  settings: DataCoveragePayload["settings"];
  rows: DataApiUsageRow[];
};

export type DataFetchJob = {
  id: string;
  status: "queued" | "running" | "completed" | "failed" | "partial";
  channel: DataFetchChannel;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  total_assets: number;
  completed_assets: number;
  skipped_assets: number;
  failed_assets: number;
  results: Array<Record<string, unknown>>;
  errors: Array<Record<string, unknown>>;
};

export type DataFetchPayload = {
  channel: DataFetchChannel;
  assets: Array<{ symbol_code: string; source_name: string }>;
  fallback_days: number;
  overlap_minutes: number;
  neon_limit: number;
  max_databento_usd: number;
};

export type BacktestLaunchPayload = {
  strategy_config?: string;
  strategy_definition_id?: string;
  assets: Array<{ symbol_code: string; source_name: string }>;
  from: string;
  to: string;
  timeframe: string;
  label?: string;
};

export type StrategyBlock = {
  id: string;
  type: string;
  label?: string | null;
  timeframe?: string | null;
  enabled: boolean;
  params: Record<string, unknown>;
  outputs: string[];
};

export type StrategyDefinitionPayload = {
  global_params: Record<string, unknown>;
  timeframes: string[];
  blocks: StrategyBlock[];
  execution: Record<string, unknown>;
};

export type StrategyDefinition = {
  id: string;
  name: string;
  version: string;
  status: "draft" | "validated" | "archived";
  description?: string | null;
  definition: StrategyDefinitionPayload;
  definition_hash: string;
  exported_path?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type StrategyBuilderCatalog = {
  blocks: Record<
    string,
    {
      label: string;
      category: string;
      timeframes: string[];
      outputs: string[];
      params: Record<string, unknown>;
      experimental?: boolean;
    }
  >;
  timeframes: string[];
  templates: Array<{ id: string; name: string; description: string }>;
};

export type StrategyValidationResult = {
  valid: boolean;
  errors: string[];
  warnings: string[];
  definition_hash?: string | null;
};

export type RunAnalytics = {
  run: {
    id: string;
    status: string;
    run_type: string;
    start_time: string;
    end_time: string;
    created_at: string;
    symbol_code: string;
    source_name: string;
    strategy_name: string;
    strategy_version: string;
    parameter_set_name: string;
    timeframe: string;
  };
  summary: AnalyticsSummary;
  equity_curve: AnalyticsPoint[];
  cumulative_pnl: AnalyticsPoint[];
  monthly: Array<{
    month: string;
    trades: number;
    pnl: number;
    winrate: number | null;
    avg_rr: number | null;
    expectancy: number | null;
  }>;
  breakdowns: {
    direction: AnalyticsBreakdownRow[];
    pd_type: AnalyticsBreakdownRow[];
    target_source: AnalyticsBreakdownRow[];
    session: AnalyticsBreakdownRow[];
    exit_reason: AnalyticsBreakdownRow[];
    hour_of_day: AnalyticsBreakdownRow[];
    day_of_week: AnalyticsBreakdownRow[];
    symbol: AnalyticsBreakdownRow[];
    source: AnalyticsBreakdownRow[];
  };
  comparisons: {
    symbols: AnalyticsBreakdownRow[];
    sources: AnalyticsBreakdownRow[];
  };
  rr_distribution: Array<{ bucket: string; trades: number; pnl: number }>;
  event_funnel: Array<{ event_type: string; count: number }>;
  diagnostics: Array<{ severity: "info" | "warning" | "critical"; title: string; detail: string }>;
  group?: {
    id: string;
    label: string;
    run_count: number;
    active_run_count: number;
    run_ids: string[];
    symbols: string[];
    selected_symbols: string[];
  };
  available_symbols?: string[];
  selected_symbols?: string[];
  source: { database: string; generated_at: string };
};
