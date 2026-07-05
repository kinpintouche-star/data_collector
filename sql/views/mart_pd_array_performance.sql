CREATE OR REPLACE VIEW mart_pd_array_performance AS
SELECT
    run_id,
    dataset_id,
    pd_type,
    COUNT(*) AS trades,
    SUM(pnl) AS pnl,
    AVG(rr) AS avg_rr,
    COUNT(*) FILTER (WHERE pnl > 0)::numeric / NULLIF(COUNT(*), 0) AS winrate
FROM trades
GROUP BY run_id, dataset_id, pd_type;
