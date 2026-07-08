from __future__ import annotations

import pandas as pd

from ict.data.normalizer import ColumnMappingTransformer, TransformContext


def test_candle_normalization_to_utc() -> None:
    raw = pd.DataFrame(
        {
            "DateTime": ["2025-01-01 09:30:00"],
            "Open": [100],
            "High": [105],
            "Low": [99],
            "Close": [104],
            "Volume": [123],
        }
    )
    transformer = ColumnMappingTransformer()
    context = TransformContext(
        source_name="csv",
        symbol_code="GER40",
        source_symbol="DAX_GER40_M1",
        timeframe="M1",
        source_timezone="Europe/Paris",
        column_mapping={
            "time": "DateTime",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "tick_volume": "Volume",
        },
    )

    normalized = transformer.transform(raw, context)

    assert normalized.iloc[0]["time_open"] == pd.Timestamp("2025-01-01 08:30:00", tz="UTC")
    assert normalized.iloc[0]["open"] == 100
    assert normalized.iloc[0]["source_metadata"]["source_symbol"] == "DAX_GER40_M1"


def test_candle_normalization_epoch_milliseconds() -> None:
    raw = pd.DataFrame(
        {
            "timestamp": [1767571200000],
            "open": [1.1705],
            "high": [1.17075],
            "low": [1.17046],
            "close": [1.17062],
        }
    )
    transformer = ColumnMappingTransformer()
    context = TransformContext(
        source_name="dukascopy",
        symbol_code="EURUSD",
        source_symbol="eurusd",
        timeframe="M1",
        source_timezone="UTC",
        time_unit="ms",
        column_mapping={
            "time": "timestamp",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
        },
    )

    normalized = transformer.transform(raw, context)

    assert normalized.iloc[0]["time_open"] == pd.Timestamp("2026-01-05 00:00:00", tz="UTC")
    assert normalized.iloc[0]["close"] == 1.17062
