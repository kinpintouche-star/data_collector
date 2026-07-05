from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from ict.data.candles import normalize_candles


@dataclass(frozen=True)
class PriceZone:
    kind: str
    direction: Literal["bullish", "bearish"]
    bottom: float
    top: float
    created_time: pd.Timestamp
    source_time: pd.Timestamp
    metadata: dict

    @property
    def midpoint(self) -> float:
        return (self.bottom + self.top) / 2.0


def detect_fvgs(candles: pd.DataFrame) -> list[PriceZone]:
    df = normalize_candles(candles)
    zones: list[PriceZone] = []
    for idx in range(2, len(df)):
        current = df.iloc[idx]
        two_back = df.iloc[idx - 2]
        if float(current["low"]) > float(two_back["high"]):
            zones.append(
                PriceZone(
                    kind="FVG",
                    direction="bullish",
                    bottom=float(two_back["high"]),
                    top=float(current["low"]),
                    created_time=pd.Timestamp(current["time_open"]),
                    source_time=pd.Timestamp(two_back["time_open"]),
                    metadata={"index": idx},
                )
            )
        if float(current["high"]) < float(two_back["low"]):
            zones.append(
                PriceZone(
                    kind="FVG",
                    direction="bearish",
                    bottom=float(current["high"]),
                    top=float(two_back["low"]),
                    created_time=pd.Timestamp(current["time_open"]),
                    source_time=pd.Timestamp(two_back["time_open"]),
                    metadata={"index": idx},
                )
            )
    return zones


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def _cross_up(series: pd.Series, threshold: pd.Series) -> pd.Series:
    return (series.shift(1) <= threshold.shift(1)) & (series > threshold)


def _cross_down(series: pd.Series, threshold: pd.Series) -> pd.Series:
    return (series.shift(1) >= threshold.shift(1)) & (series < threshold)


def _body_ratio(row: pd.Series) -> float:
    candle_range = float(row["high"] - row["low"])
    if candle_range <= 0:
        return 0.0
    return abs(float(row["close"] - row["open"])) / candle_range


def _find_ob_candle(
    df: pd.DataFrame,
    idx: int,
    direction: Literal["bullish", "bearish"],
    offsets: range,
    min_body_ratio: float,
) -> int | None:
    for offset in offsets:
        candidate_idx = idx - offset
        if candidate_idx < 0:
            break
        row = df.iloc[candidate_idx]
        is_red = float(row["close"]) < float(row["open"])
        is_green = float(row["close"]) > float(row["open"])
        wanted = is_red if direction == "bullish" else is_green
        if wanted and _body_ratio(row) >= min_body_ratio:
            return candidate_idx
    return None


def _make_ob(df: pd.DataFrame, trigger_idx: int, candle_idx: int, direction: str, source: str) -> PriceZone:
    candle = df.iloc[candle_idx]
    return PriceZone(
        kind="OB",
        direction=direction,  # type: ignore[arg-type]
        bottom=float(candle["low"]),
        top=float(candle["high"]),
        created_time=pd.Timestamp(df.iloc[trigger_idx]["time_open"]),
        source_time=pd.Timestamp(candle["time_open"]),
        metadata={"trigger_index": trigger_idx, "candle_index": candle_idx, "source": source},
    )


def detect_order_blocks(
    candles: pd.DataFrame,
    sensitivity_mode: Literal["ATR_ADAPTIVE", "PERCENT"] = "ATR_ADAPTIVE",
    atr_len: int = 14,
    ob1_sensitivity: float = 5.0,
    ob2_sensitivity: float = 8.0,
    ob_min_body_ratio: float = 0.55,
    ob_lookback1: int = 10,
    ob_lookback2_from: int = 4,
    ob_lookback2_to: int = 15,
) -> list[PriceZone]:
    df = normalize_candles(candles)
    if len(df) < 5:
        return []
    numeric = df.copy()
    for column in ["open", "high", "low", "close"]:
        numeric[column] = pd.to_numeric(numeric[column])

    atr_pct = _true_range(numeric).rolling(atr_len, min_periods=1).mean() / numeric["close"] * 100
    if sensitivity_mode == "ATR_ADAPTIVE":
        ob1_threshold = ob1_sensitivity * 0.1 * atr_pct
        ob2_threshold = ob2_sensitivity * 0.1 * atr_pct
    elif sensitivity_mode == "PERCENT":
        ob1_threshold = pd.Series(ob1_sensitivity * 0.01, index=numeric.index)
        ob2_threshold = pd.Series(ob2_sensitivity * 0.01, index=numeric.index)
    else:
        raise ValueError(f"Unsupported OB sensitivity mode: {sensitivity_mode}")

    ob1_pc = (numeric["close"] - numeric["close"].shift(1)) / numeric["close"].shift(1) * 100
    ob2_pc = (numeric["open"] - numeric["open"].shift(4)) / numeric["open"].shift(4) * 100

    zones: list[PriceZone] = []
    bullish_ob1 = _cross_up(ob1_pc, ob1_threshold)
    bearish_ob1 = _cross_down(ob1_pc, -ob1_threshold)
    bullish_ob2 = _cross_up(ob2_pc, ob2_threshold)
    bearish_ob2 = _cross_down(ob2_pc, -ob2_threshold)

    for idx in range(len(numeric)):
        if bool(bullish_ob1.iloc[idx]):
            candle_idx = _find_ob_candle(numeric, idx, "bullish", range(1, ob_lookback1 + 1), ob_min_body_ratio)
            if candle_idx is not None:
                zones.append(_make_ob(numeric, idx, candle_idx, "bullish", "OB1"))
        if bool(bearish_ob1.iloc[idx]):
            candle_idx = _find_ob_candle(numeric, idx, "bearish", range(1, ob_lookback1 + 1), ob_min_body_ratio)
            if candle_idx is not None:
                zones.append(_make_ob(numeric, idx, candle_idx, "bearish", "OB1"))
        if bool(bullish_ob2.iloc[idx]):
            candle_idx = _find_ob_candle(
                numeric,
                idx,
                "bullish",
                range(ob_lookback2_from, ob_lookback2_to + 1),
                ob_min_body_ratio,
            )
            if candle_idx is not None:
                zones.append(_make_ob(numeric, idx, candle_idx, "bullish", "OB2"))
        if bool(bearish_ob2.iloc[idx]):
            candle_idx = _find_ob_candle(
                numeric,
                idx,
                "bearish",
                range(ob_lookback2_from, ob_lookback2_to + 1),
                ob_min_body_ratio,
            )
            if candle_idx is not None:
                zones.append(_make_ob(numeric, idx, candle_idx, "bearish", "OB2"))
    return zones


def overlaps(zone: PriceZone, bottom: float, top: float) -> bool:
    return zone.top >= bottom and zone.bottom <= top


def zone_is_selectable(
    zone: PriceZone,
    direction: str,
    s2_time: pd.Timestamp,
    leg_end_time: pd.Timestamp,
    ote_bottom: float,
    ote_top: float,
    require_midpoint: bool = True,
) -> bool:
    if zone.direction != direction:
        return False
    if not (s2_time <= zone.source_time <= leg_end_time):
        return False
    if require_midpoint:
        return ote_bottom <= zone.midpoint <= ote_top
    return overlaps(zone, ote_bottom, ote_top)


def select_pd_array(
    obs: list[PriceZone],
    fvgs: list[PriceZone],
    direction: str,
    s2_time: pd.Timestamp,
    leg_end_time: pd.Timestamp,
    ote_bottom: float,
    ote_top: float,
    pd_mode: Literal["OB_SOLID", "FVG", "OB_OR_FVG"] = "OB_OR_FVG",
    require_midpoint: bool = True,
) -> PriceZone | None:
    def candidates(zones: list[PriceZone]) -> list[PriceZone]:
        return [
            zone
            for zone in zones
            if zone_is_selectable(
                zone,
                direction,
                s2_time,
                leg_end_time,
                ote_bottom,
                ote_top,
                require_midpoint=require_midpoint,
            )
        ]

    if pd_mode == "OB_SOLID":
        return next(iter(reversed(candidates(obs))), None)
    if pd_mode == "FVG":
        return next(iter(reversed(candidates(fvgs))), None)
    if pd_mode == "OB_OR_FVG":
        return next(iter(reversed(candidates(obs))), None) or next(iter(reversed(candidates(fvgs))), None)
    raise ValueError(f"Unsupported PD mode: {pd_mode}")
