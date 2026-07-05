CREATE OR REPLACE VIEW mart_dataset_quality AS
SELECT
    d.id AS dataset_id,
    s.symbol_code,
    ds.name AS source_name,
    d.timeframe,
    d.start_time,
    d.end_time,
    d.candles_count,
    d.missing_candles_count,
    d.duplicate_candles_count,
    d.quality_score,
    d.checksum,
    d.status,
    d.created_at
FROM datasets d
JOIN symbols s ON s.id = d.symbol_id
JOIN data_sources ds ON ds.id = d.source_id;
