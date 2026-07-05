from __future__ import annotations

import pandas as pd

from ict.backtest.metrics import summarize_trades


def test_summarize_trades_includes_v2_metrics() -> None:
    trades = pd.DataFrame(
        {
            "entry_time": pd.to_datetime(
                ["2025-01-01 00:00", "2025-01-01 00:10", "2025-01-01 00:20"],
                utc=True,
            ),
            "exit_time": pd.to_datetime(
                ["2025-01-01 00:05", "2025-01-01 00:15", "2025-01-01 00:40"],
                utc=True,
            ),
            "pnl": [-10, -5, 20],
            "rr": [-1, -0.5, 2],
        }
    )

    metrics = summarize_trades(trades)

    assert metrics["median_rr"] == -0.5
    assert metrics["max_consecutive_losses"] == 2
    assert metrics["avg_trade_duration_seconds"] == 600.0
    assert metrics["best_trade"] == 20.0
    assert metrics["worst_trade"] == -10.0
