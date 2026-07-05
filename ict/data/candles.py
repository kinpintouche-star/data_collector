from __future__ import annotations

from datetime import timezone

import pandas as pd


REQUIRED_CANDLE_COLUMNS = ["time_open", "open", "high", "low", "close"]


def normalize_candles(df: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in REQUIRED_CANDLE_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"Missing candle columns: {', '.join(missing)}")

    out = df.copy()
    out["time_open"] = pd.to_datetime(out["time_open"], utc=True)
    out = out.sort_values("time_open").drop_duplicates("time_open", keep="last")

    for column in ["open", "high", "low", "close"]:
        out[column] = pd.to_numeric(out[column], errors="raise")

    for column in ["tick_volume", "real_volume"]:
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce").fillna(0).astype("int64")
        else:
            out[column] = 0

    if "spread" in out.columns:
        out["spread"] = pd.to_numeric(out["spread"], errors="coerce").fillna(0).astype("int64")
    else:
        out["spread"] = 0

    return out.reset_index(drop=True)


def timeframe_delta(timeframe: str) -> pd.Timedelta:
    normalized = timeframe.upper()
    if normalized == "M1":
        return pd.Timedelta(minutes=1)
    if normalized == "M15":
        return pd.Timedelta(minutes=15)
    if normalized == "H1":
        return pd.Timedelta(hours=1)
    if normalized == "D1":
        return pd.Timedelta(days=1)
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def utc_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz=timezone.utc)
