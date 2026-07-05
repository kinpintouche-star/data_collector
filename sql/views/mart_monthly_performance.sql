CREATE OR REPLACE VIEW mart_monthly_performance AS
SELECT
    run_id,
    dataset_id,
    date_trunc('month', exit_time) AS month,
    COUNT(*) AS trades,
    SUM(pnl) AS pnl,
    AVG(rr) AS avg_rr,
    COUNT(*) FILTER (WHERE pnl > 0) AS wins,
    COUNT(*) FILTER (WHERE pnl < 0) AS losses
FROM trades
WHERE exit_time IS NOT NULL
GROUP BY run_id, dataset_id, date_trunc('month', exit_time);
