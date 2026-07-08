from __future__ import annotations

import pandas as pd

from ict.data.gaps import split_continuous_candles


def test_split_continuous_candles_detects_and_drops_short_segments() -> None:
    times = [
        *pd.date_range("2026-01-01 00:00", periods=130, freq="min", tz="UTC"),
        *pd.date_range("2026-01-01 03:00", periods=10, freq="min", tz="UTC"),
    ]
    frame = pd.DataFrame(
        {
            "time_open": times,
            "open": 1.0,
            "high": 1.1,
            "low": 0.9,
            "close": 1.0,
        }
    )

    plan = split_continuous_candles(frame, "M1", min_segment_rows=120)

    assert len(plan.gaps) == 1
    assert plan.gaps[0].missing_candles == 50
    assert len(plan.segments) == 1
    assert plan.segments[0].rows == 130
    assert len(plan.dropped_segments) == 1
    assert plan.dropped_rows == 10
