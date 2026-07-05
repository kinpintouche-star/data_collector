from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from ict.data.candles import normalize_candles, timeframe_delta


@dataclass(frozen=True)
class Pivot:
    kind: Literal["high", "low"]
    pivot_time: pd.Timestamp
    confirmation_time: pd.Timestamp
    price: float
    left_price: float
    right_price: float


@dataclass(frozen=True)
class CrtSignal:
    direction: Literal["bullish", "bearish"]
    c1_high: float
    c1_low: float
    c1_mid: float
    c2_close: float
    is_c3: bool = False


def detect_pivots(candles: pd.DataFrame, timeframe: str = "M15") -> list[Pivot]:
    df = normalize_candles(candles)
    if len(df) < 3:
        return []
    delta = timeframe_delta(timeframe)
    pivots: list[Pivot] = []
    for idx in range(1, len(df) - 1):
        left = df.iloc[idx - 1]
        middle = df.iloc[idx]
        right = df.iloc[idx + 1]
        confirmation_time = pd.Timestamp(right["time_open"]) + delta
        if middle["high"] > left["high"] and middle["high"] > right["high"]:
            pivots.append(
                Pivot(
                    kind="high",
                    pivot_time=pd.Timestamp(middle["time_open"]),
                    confirmation_time=confirmation_time,
                    price=float(middle["high"]),
                    left_price=float(left["high"]),
                    right_price=float(right["high"]),
                )
            )
        if middle["low"] < left["low"] and middle["low"] < right["low"]:
            pivots.append(
                Pivot(
                    kind="low",
                    pivot_time=pd.Timestamp(middle["time_open"]),
                    confirmation_time=confirmation_time,
                    price=float(middle["low"]),
                    left_price=float(left["low"]),
                    right_price=float(right["low"]),
                )
            )
    return pivots


def pivots_frame(candles: pd.DataFrame, timeframe: str = "M15") -> pd.DataFrame:
    return pd.DataFrame([pivot.__dict__ for pivot in detect_pivots(candles, timeframe)])


def fib_level(start_price: float, end_price: float, level: float) -> float:
    price_range = abs(end_price - start_price)
    if end_price > start_price:
        return end_price - price_range * level
    return end_price + price_range * level


def ote_zone(start_price: float, end_price: float, deep: float = 0.79) -> tuple[float, float]:
    p618 = fib_level(start_price, end_price, 0.618)
    pdeep = fib_level(start_price, end_price, deep)
    return min(p618, pdeep), max(p618, pdeep)


def crt_signal(
    c1: dict[str, float],
    c2: dict[str, float],
    detect_c3: bool = True,
    model: Literal["sweep_back_in", "body_inside"] = "sweep_back_in",
) -> CrtSignal | None:
    c1_high = float(c1["high"])
    c1_low = float(c1["low"])
    c1_mid = (c1_high + c1_low) / 2.0
    c2_open = float(c2["open"])
    c2_high = float(c2["high"])
    c2_low = float(c2["low"])
    c2_close = float(c2["close"])

    if model == "body_inside":
        body_high = max(c2_open, c2_close)
        body_low = min(c2_open, c2_close)
        inside = body_high <= c1_high and body_low >= c1_low
        if inside and c2_close > c2_open:
            return CrtSignal("bullish", c1_high, c1_low, c1_mid, c2_close)
        if inside and c2_close < c2_open:
            return CrtSignal("bearish", c1_high, c1_low, c1_mid, c2_close)
        return None

    back_in_c1 = c1_low < c2_close < c1_high
    swept_high = c2_high > c1_high
    swept_low = c2_low < c1_low
    bear_c2 = back_in_c1 and swept_high and (not swept_low or c2_close < c1_mid)
    bull_c2 = back_in_c1 and swept_low and (not swept_high or c2_close >= c1_mid)
    bear_c3 = detect_c3 and swept_high and c2_close < c1_low
    bull_c3 = detect_c3 and swept_low and c2_close > c1_high

    if bear_c2 or bear_c3:
        return CrtSignal("bearish", c1_high, c1_low, c1_mid, c2_close, is_c3=bear_c3)
    if bull_c2 or bull_c3:
        return CrtSignal("bullish", c1_high, c1_low, c1_mid, c2_close, is_c3=bull_c3)
    return None


def s2_inside_rule(direction: str, s1_price: float, s2_price: float) -> bool:
    if direction == "bearish":
        return s2_price < s1_price
    if direction == "bullish":
        return s2_price > s1_price
    raise ValueError(f"Unsupported direction: {direction}")


def s2_invalidated(direction: str, close: float, s2_price: float) -> bool:
    if direction == "bearish":
        return close > s2_price
    if direction == "bullish":
        return close < s2_price
    raise ValueError(f"Unsupported direction: {direction}")


def pd_touched(candle: pd.Series | dict[str, float], pd_bottom: float, pd_top: float) -> bool:
    return float(candle["high"]) >= pd_bottom and float(candle["low"]) <= pd_top


def rejection_confirmed(
    direction: str,
    candle: pd.Series | dict[str, float],
    pd_mid: float,
    pd_mitigated: bool,
) -> bool:
    if not pd_mitigated:
        return False
    open_ = float(candle["open"])
    close = float(candle["close"])
    if direction == "bullish":
        return close > open_ and close >= pd_mid
    if direction == "bearish":
        return close < open_ and close <= pd_mid
    raise ValueError(f"Unsupported direction: {direction}")


def risk_is_valid(direction: str, entry_price: float, sl: float, tp: float) -> bool:
    if direction == "bullish":
        return sl < entry_price and tp > entry_price
    if direction == "bearish":
        return sl > entry_price and tp < entry_price
    raise ValueError(f"Unsupported direction: {direction}")
