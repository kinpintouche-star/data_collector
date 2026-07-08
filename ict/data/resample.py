from __future__ import annotations

import pandas as pd

from ict.data.candles import normalize_candles


PANDAS_RULES = {
    "M1": "1min",
    "M5": "5min",
    "M15": "15min",
    "M30": "30min",
    "H1": "1h",
    "H4": "4h",
    "D1": "1D",
}


def resample_ohlcv(candles: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    timeframe = timeframe.upper()
    if timeframe not in PANDAS_RULES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    normalized = normalize_candles(candles)
    if timeframe == "M1":
        return normalized

    indexed = normalized.set_index("time_open")
    aggregated = indexed.resample(PANDAS_RULES[timeframe], label="left", closed="left").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "tick_volume": "sum",
            "spread": "last",
            "real_volume": "sum",
        }
    )
    aggregated = aggregated.dropna(subset=["open", "high", "low", "close"])
    return aggregated.reset_index()


def build_timeframes(
    m1_candles: pd.DataFrame,
    timeframes: tuple[str, ...] = ("M15", "H1"),
) -> dict[str, pd.DataFrame]:
    return {timeframe: resample_ohlcv(m1_candles, timeframe) for timeframe in timeframes}
