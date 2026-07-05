CREATE OR REPLACE VIEW mart_session_performance AS
SELECT
    run_id,
    dataset_id,
    COALESCE(session_name, 'Other') AS session_name,
    COUNT(*) AS trades,
    SUM(pnl) AS pnl,
    AVG(rr) AS avg_rr,
    COUNT(*) FILTER (WHERE pnl > 0)::numeric / NULLIF(COUNT(*), 0) AS winrate
FROM trades
GROUP BY run_id, dataset_id, COALESCE(session_name, 'Other');
