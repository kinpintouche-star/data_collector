CREATE OR REPLACE VIEW mart_symbol_performance AS
SELECT
    t.symbol_id,
    s.symbol_code,
    COUNT(*) AS trades,
    SUM(t.pnl) AS pnl,
    AVG(t.rr) AS avg_rr,
    COUNT(*) FILTER (WHERE t.pnl > 0)::numeric / NULLIF(COUNT(*), 0) AS winrate
FROM trades t
JOIN symbols s ON s.id = t.symbol_id
GROUP BY t.symbol_id, s.symbol_code;
