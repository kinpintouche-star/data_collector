CREATE OR REPLACE VIEW mart_live_collector AS
SELECT
    css.id AS state_id,
    s.symbol_code,
    s.asset_type,
    ds.name AS source_name,
    css.source_symbol,
    css.provider,
    css.timeframe,
    css.priority,
    css.enabled,
    css.poll_interval_minutes,
    css.retention_days,
    css.collection_mode,
    css.status,
    css.last_candle_time,
    css.last_success_at,
    css.last_error_at,
    css.last_error_message,
    css.consecutive_failures,
    css.lag_seconds,
    css.updated_at,
    COUNT(ci.id) FILTER (WHERE ci.status = 'open') AS open_incidents,
    MAX(ci.severity) FILTER (WHERE ci.status = 'open') AS highest_open_severity
FROM collector_source_state css
JOIN symbols s ON s.id = css.symbol_id
JOIN data_sources ds ON ds.id = css.source_id
LEFT JOIN collector_incidents ci
    ON ci.symbol_id = css.symbol_id
    AND ci.source_id = css.source_id
    AND ci.timeframe = css.timeframe
    AND ci.status = 'open'
GROUP BY
    css.id,
    s.symbol_code,
    s.asset_type,
    ds.name,
    css.source_symbol,
    css.provider,
    css.timeframe,
    css.priority,
    css.enabled,
    css.poll_interval_minutes,
    css.retention_days,
    css.collection_mode,
    css.status,
    css.last_candle_time,
    css.last_success_at,
    css.last_error_at,
    css.last_error_message,
    css.consecutive_failures,
    css.lag_seconds,
    css.updated_at;
