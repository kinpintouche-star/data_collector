CREATE OR REPLACE VIEW mart_parameter_performance AS
SELECT
    r.parameter_set_id,
    ps.name,
    ps.params,
    COUNT(t.id) AS trades,
    SUM(t.pnl) AS pnl,
    AVG(t.rr) AS avg_rr,
    COUNT(t.id) FILTER (WHERE t.pnl > 0)::numeric / NULLIF(COUNT(t.id), 0) AS winrate,
    SUM(t.pnl) FILTER (WHERE t.pnl > 0) / NULLIF(ABS(SUM(t.pnl) FILTER (WHERE t.pnl < 0)), 0) AS profit_factor
FROM backtest_runs r
JOIN parameter_sets ps ON ps.id = r.parameter_set_id
LEFT JOIN trades t ON t.run_id = r.id
GROUP BY r.parameter_set_id, ps.name, ps.params;
