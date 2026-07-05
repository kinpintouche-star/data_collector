CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS data_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    description TEXT,
    base_timezone TEXT DEFAULT 'UTC',
    priority INTEGER DEFAULT 100,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS symbols (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol_code TEXT NOT NULL UNIQUE,
    asset_type TEXT NOT NULL,
    description TEXT,
    base_currency TEXT,
    quote_currency TEXT,
    price_currency TEXT,
    exchange TEXT,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    tick_size NUMERIC,
    tick_value NUMERIC,
    point_size NUMERIC,
    contract_size NUMERIC,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS symbol_aliases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol_id UUID NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    source_id UUID NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
    source_symbol TEXT NOT NULL,
    source_exchange TEXT,
    source_asset_type TEXT,
    source_timezone TEXT,
    min_timeframe TEXT,
    max_timeframe TEXT,
    price_multiplier NUMERIC NOT NULL DEFAULT 1,
    tick_size_override NUMERIC,
    point_size_override NUMERIC,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(source_id, source_symbol),
    UNIQUE(symbol_id, source_id, source_symbol)
);

CREATE INDEX IF NOT EXISTS idx_symbol_aliases_symbol_source
ON symbol_aliases(symbol_id, source_id);

CREATE TABLE IF NOT EXISTS market_candles (
    id BIGSERIAL PRIMARY KEY,
    symbol_id UUID NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    source_id UUID NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
    timeframe TEXT NOT NULL,
    time_open TIMESTAMPTZ NOT NULL,
    open NUMERIC NOT NULL,
    high NUMERIC NOT NULL,
    low NUMERIC NOT NULL,
    close NUMERIC NOT NULL,
    tick_volume BIGINT,
    real_volume BIGINT,
    spread NUMERIC,
    source_symbol TEXT,
    quality_flags JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(symbol_id, source_id, timeframe, time_open)
);

CREATE INDEX IF NOT EXISTS idx_market_candles_symbol_source_tf_time
ON market_candles(symbol_id, source_id, timeframe, time_open);

CREATE INDEX IF NOT EXISTS idx_market_candles_time
ON market_candles(time_open);

CREATE TABLE IF NOT EXISTS data_import_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL REFERENCES data_sources(id),
    symbol_id UUID NOT NULL REFERENCES symbols(id),
    alias_id UUID REFERENCES symbol_aliases(id),
    timeframe TEXT NOT NULL,
    requested_start TIMESTAMPTZ NOT NULL,
    requested_end TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'created',
    rows_fetched INTEGER DEFAULT 0,
    rows_inserted INTEGER DEFAULT 0,
    rows_updated INTEGER DEFAULT 0,
    rows_skipped INTEGER DEFAULT 0,
    error_message TEXT,
    source_params JSONB NOT NULL DEFAULT '{}'::jsonb,
    quality_report JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS datasets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol_id UUID NOT NULL REFERENCES symbols(id),
    source_id UUID NOT NULL REFERENCES data_sources(id),
    timeframe TEXT NOT NULL,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    dataset_name TEXT,
    dataset_version TEXT NOT NULL DEFAULT '1',
    candles_count INTEGER,
    missing_candles_count INTEGER,
    duplicate_candles_count INTEGER,
    quality_score NUMERIC,
    checksum TEXT,
    status TEXT NOT NULL DEFAULT 'created',
    notes TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(symbol_id, source_id, timeframe, start_time, end_time, dataset_version)
);

CREATE INDEX IF NOT EXISTS idx_datasets_symbol_source_time
ON datasets(symbol_id, source_id, timeframe, start_time, end_time);

CREATE TABLE IF NOT EXISTS strategy_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'python',
    source_reference TEXT,
    git_commit TEXT,
    description TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(name, version)
);

CREATE TABLE IF NOT EXISTS parameter_sets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_version_id UUID REFERENCES strategy_versions(id),
    name TEXT NOT NULL,
    params JSONB NOT NULL,
    params_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_parameter_sets_params_gin
ON parameter_sets USING GIN(params);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy_version_id UUID NOT NULL REFERENCES strategy_versions(id),
    parameter_set_id UUID NOT NULL REFERENCES parameter_sets(id),
    dataset_id UUID NOT NULL REFERENCES datasets(id),
    symbol_id UUID NOT NULL REFERENCES symbols(id),
    source_id UUID NOT NULL REFERENCES data_sources(id),
    run_type TEXT NOT NULL DEFAULT 'backtest',
    status TEXT NOT NULL DEFAULT 'created',
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    initial_balance NUMERIC NOT NULL,
    final_balance NUMERIC,
    error_message TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_dataset_created
ON backtest_runs(dataset_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_symbol_created
ON backtest_runs(symbol_id, created_at DESC);

CREATE TABLE IF NOT EXISTS setup_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    dataset_id UUID NOT NULL REFERENCES datasets(id),
    symbol_id UUID NOT NULL REFERENCES symbols(id),
    source_id UUID NOT NULL REFERENCES data_sources(id),
    setup_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_time TIMESTAMPTZ NOT NULL,
    direction TEXT,
    price NUMERIC,
    state_before TEXT,
    state_after TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_setup_events_run_time
ON setup_events(run_id, event_time);

CREATE INDEX IF NOT EXISTS idx_setup_events_type
ON setup_events(event_type);

CREATE INDEX IF NOT EXISTS idx_setup_events_setup
ON setup_events(run_id, setup_id);

CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    dataset_id UUID NOT NULL REFERENCES datasets(id),
    setup_id TEXT,
    symbol_id UUID NOT NULL REFERENCES symbols(id),
    source_id UUID NOT NULL REFERENCES data_sources(id),
    order_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    requested_time TIMESTAMPTZ NOT NULL,
    requested_price NUMERIC,
    volume NUMERIC NOT NULL,
    sl NUMERIC,
    tp NUMERIC,
    status TEXT NOT NULL,
    external_order_id TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    dataset_id UUID NOT NULL REFERENCES datasets(id),
    order_id UUID REFERENCES orders(id),
    symbol_id UUID NOT NULL REFERENCES symbols(id),
    source_id UUID NOT NULL REFERENCES data_sources(id),
    fill_time TIMESTAMPTZ NOT NULL,
    fill_price NUMERIC NOT NULL,
    volume NUMERIC NOT NULL,
    commission NUMERIC DEFAULT 0,
    slippage NUMERIC DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    dataset_id UUID NOT NULL REFERENCES datasets(id),
    setup_id TEXT NOT NULL,
    symbol_id UUID NOT NULL REFERENCES symbols(id),
    source_id UUID NOT NULL REFERENCES data_sources(id),
    direction TEXT NOT NULL,
    entry_time TIMESTAMPTZ NOT NULL,
    entry_price NUMERIC NOT NULL,
    exit_time TIMESTAMPTZ,
    exit_price NUMERIC,
    volume NUMERIC NOT NULL,
    sl NUMERIC NOT NULL,
    tp NUMERIC NOT NULL,
    exit_reason TEXT,
    pnl NUMERIC,
    pnl_points NUMERIC,
    rr NUMERIC,
    mae NUMERIC,
    mfe NUMERIC,
    pd_type TEXT,
    strategy_mode TEXT,
    session_name TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_trades_run_entry
ON trades(run_id, entry_time);

CREATE INDEX IF NOT EXISTS idx_trades_symbol_entry
ON trades(symbol_id, entry_time);

CREATE INDEX IF NOT EXISTS idx_trades_dataset_entry
ON trades(dataset_id, entry_time);

CREATE TABLE IF NOT EXISTS equity_curve (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    dataset_id UUID NOT NULL REFERENCES datasets(id),
    time TIMESTAMPTZ NOT NULL,
    balance NUMERIC NOT NULL,
    equity NUMERIC NOT NULL,
    drawdown_abs NUMERIC,
    drawdown_pct NUMERIC,
    open_positions INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(run_id, time)
);

CREATE INDEX IF NOT EXISTS idx_equity_curve_run_time
ON equity_curve(run_id, time);

CREATE TABLE IF NOT EXISTS run_metrics (
    run_id UUID PRIMARY KEY REFERENCES backtest_runs(id) ON DELETE CASCADE,
    dataset_id UUID NOT NULL REFERENCES datasets(id),
    total_h1_signals INTEGER DEFAULT 0,
    total_setups INTEGER DEFAULT 0,
    total_legs INTEGER DEFAULT 0,
    total_pd_selected INTEGER DEFAULT 0,
    total_pd_touched INTEGER DEFAULT 0,
    total_rejections INTEGER DEFAULT 0,
    total_risk_rejected INTEGER DEFAULT 0,
    total_trades INTEGER DEFAULT 0,
    total_wins INTEGER DEFAULT 0,
    total_losses INTEGER DEFAULT 0,
    winrate NUMERIC,
    avg_rr NUMERIC,
    median_rr NUMERIC,
    profit_factor NUMERIC,
    expectancy NUMERIC,
    net_profit NUMERIC,
    max_drawdown_abs NUMERIC,
    max_drawdown_pct NUMERIC,
    max_consecutive_losses INTEGER,
    avg_trade_duration_seconds NUMERIC,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
