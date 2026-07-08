CREATE TABLE IF NOT EXISTS live_market_candles (
    source_state_id UUID NOT NULL REFERENCES collector_source_state(id) ON DELETE CASCADE,
    time_open TIMESTAMPTZ NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    tick_volume BIGINT,
    real_volume DOUBLE PRECISION,
    spread DOUBLE PRECISION,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_state_id, time_open)
);

CREATE INDEX IF NOT EXISTS idx_live_market_candles_time
ON live_market_candles(time_open DESC);
