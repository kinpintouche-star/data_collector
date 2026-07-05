from __future__ import annotations

import pandas as pd


def describe_run_summary(run_summary: pd.DataFrame) -> str:
    if run_summary.empty:
        return "No backtest runs found."
    latest = run_summary.sort_values("created_at").iloc[-1]
    return (
        f"Latest run {latest['run_id']} on {latest['symbol_code']}: "
        f"{latest.get('total_trades', 0)} trades, net profit {latest.get('net_profit')}."
    )
