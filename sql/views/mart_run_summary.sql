CREATE OR REPLACE VIEW mart_run_summary AS
SELECT
    r.id AS run_id,
    r.dataset_id,
    s.symbol_code,
    ds.name AS source_name,
    d.dataset_name,
    d.dataset_version,
    sv.name AS strategy_name,
    sv.version AS strategy_version,
    r.status,
    r.start_time,
    r.end_time,
    r.initial_balance,
    r.final_balance,
    m.total_trades,
    m.winrate,
    m.profit_factor,
    m.expectancy,
    m.net_profit,
    m.max_drawdown_pct,
    r.created_at,
    r.parameter_set_id
FROM backtest_runs r
JOIN symbols s ON s.id = r.symbol_id
JOIN data_sources ds ON ds.id = r.source_id
JOIN datasets d ON d.id = r.dataset_id
JOIN strategy_versions sv ON sv.id = r.strategy_version_id
LEFT JOIN run_metrics m ON m.run_id = r.id;
