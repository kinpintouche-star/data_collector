from __future__ import annotations

import pandas as pd

from ict.backtest.engine import BacktestEngine
from ict.strategy.params import StrategyParams


def _setbar(df: pd.DataFrame, timestamp: str, **values: float) -> None:
    idx = df.index[df["time_open"] == pd.Timestamp(timestamp, tz="UTC")][0]
    for key, value in values.items():
        df.at[idx, key] = value


def test_backtest_records_trade_and_events() -> None:
    times = pd.date_range("2025-01-01 00:00", periods=240, freq="min", tz="UTC")
    m1 = pd.DataFrame(
        {
            "time_open": times,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
        }
    )

    _setbar(m1, "2025-01-01 00:10", high=110)
    _setbar(m1, "2025-01-01 00:20", low=90)
    _setbar(m1, "2025-01-01 00:59", close=100)
    _setbar(m1, "2025-01-01 01:05", high=112)
    _setbar(m1, "2025-01-01 01:30", high=109)
    _setbar(m1, "2025-01-01 01:59", close=99)
    _setbar(m1, "2025-01-01 02:00", low=90)
    _setbar(m1, "2025-01-01 02:15", low=80)
    _setbar(m1, "2025-01-01 02:30", low=88)
    _setbar(m1, "2025-01-01 02:08", open=104, high=104, low=103, close=103.5)
    _setbar(m1, "2025-01-01 02:10", open=98, high=99, low=96, close=97)
    _setbar(m1, "2025-01-01 02:50", open=104, high=104, low=100, close=101)
    _setbar(m1, "2025-01-01 02:55", open=100, high=101, low=89, close=90)

    params = StrategyParams(
        only_killzone=False,
        pd_mode="FVG",
        strategy_mode="B_NO_S2_INVALIDATION",
        execution={"fill_policy": "signal_close", "ambiguous_bar_policy": "sl_first"},
    )
    result = BacktestEngine(params, tick_size=0.25).run(m1)
    event_types = [event["event_type"] for event in result.events]

    assert len(result.trades) == 1
    assert len(result.orders) == 1
    assert len(result.fills) == 1
    assert result.orders.iloc[0]["status"] == "filled"
    assert result.fills.iloc[0]["order_ref"] == result.orders.iloc[0]["order_ref"]
    assert result.trades.iloc[0]["exit_reason"] == "TP"
    assert result.metrics["median_rr"] is not None
    assert result.metrics["avg_trade_duration_seconds"] == 300.0
    assert "H1_SIGNAL" in event_types
    assert "M15_DOUBLE_SWING_VALIDATED" in event_types
    assert "FVG_SELECTED" in event_types
    assert "TRADE_OPENED" in event_types
    assert "TRADE_CLOSED_TP" in event_types
