from __future__ import annotations

import pandas as pd

from ict.strategy.indicators import detect_pivots


def test_pivot_high_low_confirmation() -> None:
    candles = pd.DataFrame(
        {
            "time_open": pd.date_range("2025-01-01 09:00", periods=5, freq="15min", tz="UTC"),
            "open": [10, 11, 10, 9, 10],
            "high": [11, 15, 12, 10, 11],
            "low": [9, 10, 8, 7, 8],
            "close": [10, 12, 9, 8, 10],
        }
    )

    pivots = detect_pivots(candles, "M15")
    pivot_high = next(pivot for pivot in pivots if pivot.kind == "high")
    pivot_low = next(pivot for pivot in pivots if pivot.kind == "low")

    assert pivot_high.price == 15
    assert pivot_high.pivot_time == pd.Timestamp("2025-01-01 09:15", tz="UTC")
    assert pivot_high.confirmation_time == pd.Timestamp("2025-01-01 09:45", tz="UTC")
    assert pivot_low.price == 7
    assert pivot_low.confirmation_time == pd.Timestamp("2025-01-01 10:15", tz="UTC")


def test_pivot_not_available_before_right_candle_closes() -> None:
    candles = pd.DataFrame(
        {
            "time_open": pd.date_range("2025-01-01 09:00", periods=3, freq="15min", tz="UTC"),
            "open": [10, 11, 10],
            "high": [11, 15, 12],
            "low": [9, 10, 8],
            "close": [10, 12, 9],
        }
    )

    pivot = detect_pivots(candles, "M15")[0]

    assert pivot.pivot_time == pd.Timestamp("2025-01-01 09:15", tz="UTC")
    assert pivot.confirmation_time == pd.Timestamp("2025-01-01 09:45", tz="UTC")
