from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pandas as pd

from ict.data.candles import timeframe_delta


@dataclass(frozen=True)
class DataQualityReport:
    candles_count: int
    missing_candles_count: int
    duplicate_candles_count: int
    invalid_ohlc_count: int
    zero_volume_count: int
    negative_spread_count: int
    sorted: bool
    utc: bool
    quality_score: float
    checksum: str
    gaps: list[dict]

    def as_dict(self) -> dict:
        return {
            "candles_count": self.candles_count,
            "missing_candles_count": self.missing_candles_count,
            "duplicate_candles_count": self.duplicate_candles_count,
            "invalid_ohlc_count": self.invalid_ohlc_count,
            "zero_volume_count": self.zero_volume_count,
            "negative_spread_count": self.negative_spread_count,
            "sorted": self.sorted,
            "utc": self.utc,
            "quality_score": self.quality_score,
            "checksum": self.checksum,
            "gaps": self.gaps,
        }


def analyze_candle_quality(frame: pd.DataFrame, timeframe: str) -> DataQualityReport:
    if frame.empty:
        return DataQualityReport(0, 0, 0, 0, 0, 0, True, True, 0.0, "", [])

    df = frame.copy()
    df["time_open"] = pd.to_datetime(df["time_open"], utc=True)
    duplicate_count = int(df.duplicated("time_open").sum())
    sorted_ok = bool(df["time_open"].is_monotonic_increasing)
    utc_ok = all(timestamp.tzinfo is not None for timestamp in df["time_open"])
    invalid_ohlc = int(
        (
            (df["high"] < df[["open", "close"]].max(axis=1))
            | (df["low"] > df[["open", "close"]].min(axis=1))
        ).sum()
    )
    if "tick_volume" in df:
        zero_volume = int((pd.to_numeric(df["tick_volume"], errors="coerce").fillna(1) == 0).sum())
    else:
        zero_volume = 0
    if "spread" in df:
        negative_spread = int((pd.to_numeric(df["spread"], errors="coerce").fillna(0) < 0).sum())
    else:
        negative_spread = 0

    sorted_times = df[["time_open"]].sort_values("time_open").reset_index(drop=True)
    expected_delta = timeframe_delta(timeframe)
    diffs = sorted_times["time_open"].diff()
    gaps = []
    missing = 0
    for idx, diff in diffs.items():
        if pd.isna(diff) or diff <= expected_delta:
            continue
        missing_here = int(diff / expected_delta) - 1
        missing += missing_here
        gaps.append(
            {
                "after": sorted_times.loc[idx - 1, "time_open"].isoformat() if idx > 0 else None,
                "before": sorted_times.loc[idx, "time_open"].isoformat(),
                "missing": missing_here,
            }
        )

    penalties = duplicate_count + invalid_ohlc + negative_spread + missing
    quality_score = max(0.0, 1.0 - penalties / max(len(df), 1))
    checksum = checksum_candles(df)
    return DataQualityReport(
        candles_count=int(len(df)),
        missing_candles_count=missing,
        duplicate_candles_count=duplicate_count,
        invalid_ohlc_count=invalid_ohlc,
        zero_volume_count=zero_volume,
        negative_spread_count=negative_spread,
        sorted=sorted_ok,
        utc=utc_ok,
        quality_score=quality_score,
        checksum=checksum,
        gaps=gaps,
    )


def checksum_candles(frame: pd.DataFrame) -> str:
    columns = ["time_open", "open", "high", "low", "close"]
    payload = frame[columns].copy()
    payload["time_open"] = pd.to_datetime(payload["time_open"], utc=True).astype(str)
    csv_payload = payload.to_csv(index=False)
    return hashlib.sha256(csv_payload.encode("utf-8")).hexdigest()


def annotate_candle_quality(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if frame.empty:
        out = frame.copy()
        out["quality_flags"] = []
        return out

    out = frame.copy()
    out["time_open"] = pd.to_datetime(out["time_open"], utc=True)
    duplicate_mask = out.duplicated("time_open", keep=False)
    high = pd.to_numeric(out["high"], errors="coerce")
    low = pd.to_numeric(out["low"], errors="coerce")
    open_ = pd.to_numeric(out["open"], errors="coerce")
    close = pd.to_numeric(out["close"], errors="coerce")
    invalid_ohlc = (high < pd.concat([open_, close], axis=1).max(axis=1)) | (
        low > pd.concat([open_, close], axis=1).min(axis=1)
    )
    zero_volume = (
        pd.to_numeric(out["tick_volume"], errors="coerce").fillna(1) == 0
        if "tick_volume" in out
        else pd.Series(False, index=out.index)
    )
    negative_spread = (
        pd.to_numeric(out["spread"], errors="coerce").fillna(0) < 0
        if "spread" in out
        else pd.Series(False, index=out.index)
    )

    gap_missing_by_time = _gap_missing_by_time(out, timeframe)
    flags = []
    for idx, row in out.iterrows():
        row_flags = {}
        if bool(duplicate_mask.loc[idx]):
            row_flags["duplicate_time"] = True
        if bool(invalid_ohlc.loc[idx]):
            row_flags["invalid_ohlc"] = True
        if bool(zero_volume.loc[idx]):
            row_flags["zero_volume"] = True
        if bool(negative_spread.loc[idx]):
            row_flags["negative_spread"] = True
        missing_before = gap_missing_by_time.get(pd.Timestamp(row["time_open"]))
        if missing_before:
            row_flags["gap_missing_before"] = missing_before
        flags.append(row_flags)
    out["quality_flags"] = flags
    return out


def prepare_candles_for_storage(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    return frame.sort_values("time_open").drop_duplicates("time_open", keep="last").reset_index(drop=True)


def _gap_missing_by_time(frame: pd.DataFrame, timeframe: str) -> dict[pd.Timestamp, int]:
    sorted_times = (
        frame[["time_open"]]
        .assign(time_open=lambda df: pd.to_datetime(df["time_open"], utc=True))
        .drop_duplicates("time_open", keep="last")
        .sort_values("time_open")
        .reset_index(drop=True)
    )
    expected_delta = timeframe_delta(timeframe)
    missing_by_time: dict[pd.Timestamp, int] = {}
    for idx, diff in sorted_times["time_open"].diff().items():
        if pd.isna(diff) or diff <= expected_delta:
            continue
        missing_by_time[pd.Timestamp(sorted_times.loc[idx, "time_open"])] = int(diff / expected_delta) - 1
    return missing_by_time
