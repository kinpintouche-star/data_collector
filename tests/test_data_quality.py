from __future__ import annotations

import pandas as pd

from ict.data.quality import analyze_candle_quality, annotate_candle_quality, prepare_candles_for_storage


def test_data_quality_detects_gaps() -> None:
    frame = pd.DataFrame(
        {
            "time_open": pd.to_datetime(
                ["2025-01-01 00:00:00", "2025-01-01 00:01:00", "2025-01-01 00:04:00"],
                utc=True,
            ),
            "open": [1, 2, 3],
            "high": [2, 3, 4],
            "low": [0, 1, 2],
            "close": [1.5, 2.5, 3.5],
            "tick_volume": [10, 10, 10],
            "spread": [0, 0, 0],
        }
    )

    report = analyze_candle_quality(frame, "M1")

    assert report.missing_candles_count == 2
    assert report.duplicate_candles_count == 0
    assert report.quality_score < 1


def test_data_quality_flags_bad_rows_and_deduplicates_for_storage() -> None:
    frame = pd.DataFrame(
        {
            "time_open": pd.to_datetime(
                [
                    "2025-01-01 00:00:00",
                    "2025-01-01 00:00:00",
                    "2025-01-01 00:03:00",
                ],
                utc=True,
            ),
            "open": [1, 2, 5],
            "high": [2, 1, 6],
            "low": [0, 1.5, 4],
            "close": [1.5, 2.5, 5.5],
            "tick_volume": [10, 0, 10],
            "spread": [0, -1, 0],
        }
    )

    annotated = annotate_candle_quality(frame, "M1")
    report = analyze_candle_quality(annotated, "M1")
    storage = prepare_candles_for_storage(annotated)

    assert report.duplicate_candles_count == 1
    assert report.invalid_ohlc_count == 1
    assert report.zero_volume_count == 1
    assert report.negative_spread_count == 1
    assert annotated.iloc[0]["quality_flags"]["duplicate_time"] is True
    assert annotated.iloc[1]["quality_flags"]["invalid_ohlc"] is True
    assert annotated.iloc[1]["quality_flags"]["zero_volume"] is True
    assert annotated.iloc[1]["quality_flags"]["negative_spread"] is True
    assert annotated.iloc[2]["quality_flags"]["gap_missing_before"] == 2
    assert len(storage) == 2
    assert storage.iloc[0]["quality_flags"]["duplicate_time"] is True
