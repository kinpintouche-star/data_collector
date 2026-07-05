CREATE OR REPLACE VIEW mart_source_performance AS
SELECT
    t.source_id,
    ds.name AS source_name,
    t.symbol_id,
    s.symbol_code,
    COUNT(*) AS trades,
    SUM(t.pnl) AS pnl,
    AVG(t.rr) AS avg_rr,
    COUNT(*) FILTER (WHERE t.pnl > 0)::numeric / NULLIF(COUNT(*), 0) AS winrate
FROM trades t
JOIN data_sources ds ON ds.id = t.source_id
JOIN symbols s ON s.id = t.symbol_id
GROUP BY t.source_id, ds.name, t.symbol_id, s.symbol_code;
