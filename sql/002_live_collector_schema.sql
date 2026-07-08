CREATE TABLE IF NOT EXISTS collector_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trigger_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    duration_ms INTEGER,
    assets_requested INTEGER NOT NULL DEFAULT 0,
    assets_succeeded INTEGER NOT NULL DEFAULT 0,
    assets_failed INTEGER NOT NULL DEFAULT 0,
    rows_fetched INTEGER NOT NULL DEFAULT 0,
    rows_written INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_collector_runs_started
ON collector_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS collector_source_state (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol_id UUID NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    source_id UUID NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
    source_symbol TEXT NOT NULL,
    provider TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    poll_interval_minutes INTEGER NOT NULL DEFAULT 1440,
    retention_days INTEGER NOT NULL DEFAULT 180,
    collection_mode TEXT NOT NULL DEFAULT 'daily',
    status TEXT NOT NULL DEFAULT 'pending',
    last_candle_time TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_error_at TIMESTAMPTZ,
    last_error_message TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    lag_seconds INTEGER,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(symbol_id, source_id, timeframe)
);

CREATE INDEX IF NOT EXISTS idx_collector_source_state_status
ON collector_source_state(status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_collector_source_state_last_candle
ON collector_source_state(last_candle_time DESC);

CREATE TABLE IF NOT EXISTS collector_incidents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_key TEXT NOT NULL,
    symbol_id UUID REFERENCES symbols(id) ON DELETE SET NULL,
    source_id UUID REFERENCES data_sources(id) ON DELETE SET NULL,
    timeframe TEXT,
    severity TEXT NOT NULL DEFAULT 'warning',
    status TEXT NOT NULL DEFAULT 'open',
    title TEXT NOT NULL,
    message TEXT,
    failure_count INTEGER NOT NULL DEFAULT 0,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_collector_incidents_status
ON collector_incidents(status, last_seen_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_collector_incidents_open_key
ON collector_incidents(incident_key)
WHERE resolved_at IS NULL;
