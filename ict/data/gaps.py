from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ict.data.candles import normalize_candles, timeframe_delta


@dataclass(frozen=True)
class CandleGap:
    after: pd.Timestamp
    before: pd.Timestamp
    missing_candles: int

    def as_dict(self) -> dict:
        return {
            "after": self.after.isoformat(),
            "before": self.before.isoformat(),
            "missing_candles": self.missing_candles,
        }


@dataclass(frozen=True)
class CandleSegment:
    frame: pd.DataFrame
    start_time: pd.Timestamp
    end_time: pd.Timestamp
    rows: int

    def as_dict(self) -> dict:
        return {
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "rows": self.rows,
        }


@dataclass(frozen=True)
class CandleGapPlan:
    segments: list[CandleSegment]
    dropped_segments: list[CandleSegment]
    gaps: list[CandleGap]
    timeframe: str
    expected_delta_seconds: int

    @property
    def dropped_rows(self) -> int:
        return sum(segment.rows for segment in self.dropped_segments)

    @property
    def missing_candles(self) -> int:
        return sum(gap.missing_candles for gap in self.gaps)


def split_continuous_candles(
    candles: pd.DataFrame,
    timeframe: str = "M1",
    min_segment_rows: int = 120,
) -> CandleGapPlan:
    frame = normalize_candles(candles)
    expected_delta = timeframe_delta(timeframe)
    if frame.empty:
        return CandleGapPlan([], [], [], timeframe.upper(), int(expected_delta.total_seconds()))

    times = pd.to_datetime(frame["time_open"], utc=True)
    diffs = times.diff()
    gap_mask = diffs > expected_delta
    gaps = [
        CandleGap(
            after=pd.Timestamp(times.iloc[index - 1]),
            before=pd.Timestamp(times.iloc[index]),
            missing_candles=int(diffs.iloc[index] / expected_delta) - 1,
        )
        for index, has_gap in enumerate(gap_mask)
        if bool(has_gap)
    ]
    segment_ids = gap_mask.fillna(False).cumsum()

    raw_segments: list[CandleSegment] = []
    for _, group in frame.groupby(segment_ids, sort=True):
        segment = group.reset_index(drop=True)
        raw_segments.append(
            CandleSegment(
                frame=segment,
                start_time=pd.Timestamp(segment["time_open"].iloc[0]),
                end_time=pd.Timestamp(segment["time_open"].iloc[-1]),
                rows=int(len(segment)),
            )
        )

    if not gaps:
        return CandleGapPlan(raw_segments, [], gaps, timeframe.upper(), int(expected_delta.total_seconds()))

    kept = [segment for segment in raw_segments if segment.rows >= min_segment_rows]
    dropped = [segment for segment in raw_segments if segment.rows < min_segment_rows]
    return CandleGapPlan(kept, dropped, gaps, timeframe.upper(), int(expected_delta.total_seconds()))
