CREATE OR REPLACE VIEW mart_market_coverage AS
SELECT
    s.symbol_code,
    s.asset_type,
    ds.name AS source_name,
    c.timeframe,
    COUNT(*) AS candle_rows,
    MIN(c.time_open) AS first_candle_time,
    MAX(c.time_open) AS last_candle_time,
    MAX(c.ingested_at) AS last_ingested_at,
    COUNT(DISTINCT c.source_symbol) AS source_symbol_count,
    MIN(c.source_symbol) AS sample_source_symbol,
    COUNT(*) FILTER (WHERE c.quality_flags <> '{}'::jsonb) AS flagged_candles,
    AVG(c.spread) AS avg_spread
FROM market_candles c
JOIN symbols s ON s.id = c.symbol_id
JOIN data_sources ds ON ds.id = c.source_id
GROUP BY s.symbol_code, s.asset_type, ds.name, c.timeframe;
