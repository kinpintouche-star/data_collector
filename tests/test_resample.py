from __future__ import annotations

import pandas as pd

from ict.data.resample import resample_ohlcv


def test_resample_m1_to_m15_h1() -> None:
    times = pd.date_range("2025-01-01 00:00:00", periods=60, freq="min", tz="UTC")
    m1 = pd.DataFrame(
        {
            "time_open": times,
            "open": range(60),
            "high": [value + 0.5 for value in range(60)],
            "low": [value - 0.5 for value in range(60)],
            "close": [value + 0.25 for value in range(60)],
            "tick_volume": [1] * 60,
            "spread": [2] * 60,
            "real_volume": [0] * 60,
        }
    )

    m15 = resample_ohlcv(m1, "M15")
    h1 = resample_ohlcv(m1, "H1")

    assert len(m15) == 4
    assert len(h1) == 1
    assert m15.iloc[0]["open"] == 0
    assert m15.iloc[0]["high"] == 14.5
    assert m15.iloc[0]["low"] == -0.5
    assert m15.iloc[0]["close"] == 14.25
    assert m15.iloc[0]["tick_volume"] == 15
    assert h1.iloc[0]["high"] == 59.5
