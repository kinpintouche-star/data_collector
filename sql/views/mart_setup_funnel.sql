CREATE OR REPLACE VIEW mart_setup_funnel AS
SELECT
    run_id,
    dataset_id,
    COUNT(*) FILTER (WHERE event_type = 'H1_SIGNAL') AS h1_signals,
    COUNT(*) FILTER (WHERE event_type = 'M15_DOUBLE_SWING_VALIDATED') AS double_swings,
    COUNT(*) FILTER (WHERE event_type = 'LEG_FOUND') AS legs_found,
    COUNT(*) FILTER (WHERE event_type IN ('OB_SELECTED', 'FVG_SELECTED')) AS pd_selected,
    COUNT(*) FILTER (WHERE event_type = 'PD_TOUCHED') AS pd_touched,
    COUNT(*) FILTER (WHERE event_type = 'REJECTION_CONFIRMED') AS rejections,
    COUNT(*) FILTER (WHERE event_type = 'TRADE_OPENED') AS trades_opened
FROM setup_events
GROUP BY run_id, dataset_id;
