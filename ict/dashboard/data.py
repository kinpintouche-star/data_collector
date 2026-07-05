from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import pandas as pd


PAGES = ["Overview", "Runs", "Datasets", "Funnel", "Trades", "Performance", "Parameters", "Sources"]

DASHBOARD_QUERIES = {
    "Overview": "SELECT * FROM mart_run_summary ORDER BY created_at DESC LIMIT 500",
    "Runs": "SELECT * FROM mart_run_summary ORDER BY created_at DESC LIMIT 500",
    "Datasets": "SELECT * FROM mart_dataset_quality ORDER BY created_at DESC LIMIT 1000",
    "Funnel": "SELECT * FROM mart_setup_funnel ORDER BY run_id",
    "Trades": """
        SELECT t.*, s.symbol_code, ds.name AS source_name
        FROM trades t
        JOIN symbols s ON s.id = t.symbol_id
        JOIN data_sources ds ON ds.id = t.source_id
        ORDER BY t.entry_time DESC
        LIMIT 2000
    """,
    "Performance": "SELECT * FROM equity_curve ORDER BY time",
    "Parameters": "SELECT * FROM mart_parameter_performance ORDER BY profit_factor DESC NULLS LAST",
    "Sources": """
        SELECT
            r.id AS run_id,
            sv.name AS strategy_name,
            sv.version AS strategy_version,
            ds.name AS source_name,
            s.symbol_code,
            COUNT(t.id) AS trades,
            SUM(t.pnl) AS pnl,
            AVG(t.rr) AS avg_rr,
            COUNT(t.id) FILTER (WHERE t.pnl > 0)::numeric / NULLIF(COUNT(t.id), 0) AS winrate
        FROM backtest_runs r
        JOIN strategy_versions sv ON sv.id = r.strategy_version_id
        JOIN data_sources ds ON ds.id = r.source_id
        JOIN symbols s ON s.id = r.symbol_id
        LEFT JOIN trades t ON t.run_id = r.id
        GROUP BY r.id, sv.name, sv.version, ds.name, s.symbol_code
        ORDER BY pnl DESC NULLS LAST
    """,
}


def dashboard_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Convert DB-native objects to Streamlit/Arrow-friendly values."""

    if frame.empty:
        return frame
    out = frame.copy()
    for column in out.columns:
        values = out[column].dropna()
        if values.empty:
            continue
        if values.map(lambda value: isinstance(value, uuid.UUID)).any():
            out[column] = out[column].map(lambda value: str(value) if pd.notna(value) else None)
            continue
        if values.map(lambda value: isinstance(value, Decimal)).all():
            out[column] = pd.to_numeric(out[column], errors="coerce")
            continue
        if values.map(lambda value: isinstance(value, (dict, list, tuple, set))).any():
            out[column] = out[column].map(_json_cell)
    return out


def _json_cell(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, default=str, ensure_ascii=False)
    return value
