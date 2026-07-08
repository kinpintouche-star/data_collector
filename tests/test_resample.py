from __future__ import annotations

import pandas as pd

from ict.data.candles import timeframe_delta
from ict.data.resample import build_timeframes, resample_ohlcv


def test_resample_m1_to_standard_review_timeframes() -> None:
    times = pd.date_range("2025-01-01 00:00:00", periods=240, freq="min", tz="UTC")
    m1 = pd.DataFrame(
        {
            "time_open": times,
            "open": range(240),
            "high": [value + 0.5 for value in range(240)],
            "low": [value - 0.5 for value in range(240)],
            "close": [value + 0.25 for value in range(240)],
            "tick_volume": [1] * 240,
            "spread": [2] * 240,
            "real_volume": [0] * 240,
        }
    )

    m5 = resample_ohlcv(m1, "M5")
    m15 = resample_ohlcv(m1, "M15")
    m30 = resample_ohlcv(m1, "M30")
    h1 = resample_ohlcv(m1, "H1")
    h4 = resample_ohlcv(m1, "H4")

    assert len(m5) == 48
    assert len(m15) == 16
    assert len(m30) == 8
    assert len(h1) == 4
    assert len(h4) == 1
    assert m15.iloc[0]["open"] == 0
    assert m15.iloc[0]["high"] == 14.5
    assert m15.iloc[0]["low"] == -0.5
    assert m15.iloc[0]["close"] == 14.25
    assert m15.iloc[0]["tick_volume"] == 15
    assert h1.iloc[0]["high"] == 59.5
    assert h4.iloc[0]["high"] == 239.5


def test_build_timeframes_and_deltas_support_review_stack() -> None:
    times = pd.date_range("2025-01-01 00:00:00", periods=30, freq="min", tz="UTC")
    m1 = pd.DataFrame(
        {
            "time_open": times,
            "open": range(30),
            "high": range(30),
            "low": range(30),
            "close": range(30),
        }
    )

    built = build_timeframes(m1, ("M5", "M15", "M30"))

    assert set(built) == {"M5", "M15", "M30"}
    assert len(built["M5"]) == 6
    assert timeframe_delta("M5") == pd.Timedelta(minutes=5)
    assert timeframe_delta("M30") == pd.Timedelta(minutes=30)
    assert timeframe_delta("H4") == pd.Timedelta(hours=4)
