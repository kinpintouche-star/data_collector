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


@dataclass(frozen=True)
class ImmediateRebalance:
    direction: Literal["bullish", "bearish"]
    timeframe: str
    origin_time: pd.Timestamp
    impulse_time: pd.Timestamp
    rebalance_time: pd.Timestamp
    available_time: pd.Timestamp
    origin_price: float
    rebalance_price: float
    impulse_body_low: float
    impulse_body_high: float
    impulse_body_size: float
    invalidation_price: float
    tolerance: float
    metadata: dict


@dataclass(frozen=True)
class SessionRange:
    name: str
    local_date: str
    start_time: pd.Timestamp
    end_time: pd.Timestamp
    available_time: pd.Timestamp
    high: float
    low: float
    metadata: dict


@dataclass(frozen=True)
class EqualHighLow:
    kind: Literal["high", "low"]
    price: float
    first_time: pd.Timestamp
    second_time: pd.Timestamp
    available_time: pd.Timestamp
    tolerance: float
    touches: int
    metadata: dict


@dataclass(frozen=True)
class StructureBreak:
    kind: Literal["BOS", "MSS"]
    direction: Literal["bullish", "bearish"]
    timeframe: str
    break_time: pd.Timestamp
    available_time: pd.Timestamp
    level: float
    close: float
    pivot_time: pd.Timestamp
    previous_trend: str
    metadata: dict


@dataclass(frozen=True)
class AmdPhase:
    phase: Literal["accumulation_candidate", "distribution_candidate", "manipulation", "markup", "markdown"]
    direction: Literal["bullish", "bearish"]
    timeframe: str
    range_start: pd.Timestamp
    range_end: pd.Timestamp
    available_time: pd.Timestamp
    range_high: float
    range_low: float
    sweep_time: pd.Timestamp
    displacement_time: pd.Timestamp
    metadata: dict


SESSION_WINDOWS: dict[str, tuple[str, str]] = {
    "asian": ("18:00", "00:00"),
    "london": ("02:00", "05:00"),
    "new_york": ("07:00", "10:00"),
}


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


def detect_immediate_rebalances(
    candles: pd.DataFrame,
    timeframe: str = "M1",
    tick_size: float = 0.25,
    tolerance_ticks: int = 1,
    min_impulse_body_ratio: float = 0.55,
    min_impulse_body_ticks: int = 4,
    require_rejection_close: bool = True,
) -> list[ImmediateRebalance]:
    """Detect the 3-candle Immediate Rebalance pattern without using future candles."""

    df = normalize_candles(candles)
    if len(df) < 3:
        return []
    delta = timeframe_delta(timeframe)
    tolerance = max(float(tick_size) * int(tolerance_ticks), 0.0)
    min_body = max(float(tick_size) * int(min_impulse_body_ticks), 0.0)
    signals: list[ImmediateRebalance] = []

    for idx in range(2, len(df)):
        c1 = df.iloc[idx - 2]
        c2 = df.iloc[idx - 1]
        c3 = df.iloc[idx]
        impulse_body_low = min(float(c2["open"]), float(c2["close"]))
        impulse_body_high = max(float(c2["open"]), float(c2["close"]))
        impulse_body_size = impulse_body_high - impulse_body_low
        if impulse_body_size < min_body or _candle_body_ratio(c2) < min_impulse_body_ratio:
            continue

        if float(c2["close"]) > float(c2["open"]):
            signal = _bullish_immediate_rebalance(
                c1,
                c2,
                c3,
                idx,
                timeframe,
                delta,
                tolerance,
                impulse_body_low,
                impulse_body_high,
                impulse_body_size,
                require_rejection_close,
            )
            if signal is not None:
                signals.append(signal)
        elif float(c2["close"]) < float(c2["open"]):
            signal = _bearish_immediate_rebalance(
                c1,
                c2,
                c3,
                idx,
                timeframe,
                delta,
                tolerance,
                impulse_body_low,
                impulse_body_high,
                impulse_body_size,
                require_rejection_close,
            )
            if signal is not None:
                signals.append(signal)
    return signals


def _bullish_immediate_rebalance(
    c1: pd.Series,
    c2: pd.Series,
    c3: pd.Series,
    idx: int,
    timeframe: str,
    delta: pd.Timedelta,
    tolerance: float,
    impulse_body_low: float,
    impulse_body_high: float,
    impulse_body_size: float,
    require_rejection_close: bool,
) -> ImmediateRebalance | None:
    origin_price = float(c1["low"])
    wick_touch = origin_price - tolerance <= float(c3["low"]) <= origin_price + tolerance
    body_above_origin = min(float(c3["open"]), float(c3["close"])) > origin_price + tolerance
    rejected = float(c3["close"]) > float(c3["open"]) and float(c3["close"]) > origin_price
    if not wick_touch or not body_above_origin or (require_rejection_close and not rejected):
        return None
    return _make_immediate_rebalance(
        "bullish",
        c1,
        c2,
        c3,
        idx,
        timeframe,
        delta,
        origin_price,
        float(c3["low"]),
        impulse_body_low,
        impulse_body_high,
        impulse_body_size,
        impulse_body_low,
        tolerance,
    )


def _bearish_immediate_rebalance(
    c1: pd.Series,
    c2: pd.Series,
    c3: pd.Series,
    idx: int,
    timeframe: str,
    delta: pd.Timedelta,
    tolerance: float,
    impulse_body_low: float,
    impulse_body_high: float,
    impulse_body_size: float,
    require_rejection_close: bool,
) -> ImmediateRebalance | None:
    origin_price = float(c1["high"])
    wick_touch = origin_price - tolerance <= float(c3["high"]) <= origin_price + tolerance
    body_below_origin = max(float(c3["open"]), float(c3["close"])) < origin_price - tolerance
    rejected = float(c3["close"]) < float(c3["open"]) and float(c3["close"]) < origin_price
    if not wick_touch or not body_below_origin or (require_rejection_close and not rejected):
        return None
    return _make_immediate_rebalance(
        "bearish",
        c1,
        c2,
        c3,
        idx,
        timeframe,
        delta,
        origin_price,
        float(c3["high"]),
        impulse_body_low,
        impulse_body_high,
        impulse_body_size,
        impulse_body_high,
        tolerance,
    )


def _make_immediate_rebalance(
    direction: Literal["bullish", "bearish"],
    c1: pd.Series,
    c2: pd.Series,
    c3: pd.Series,
    idx: int,
    timeframe: str,
    delta: pd.Timedelta,
    origin_price: float,
    rebalance_price: float,
    impulse_body_low: float,
    impulse_body_high: float,
    impulse_body_size: float,
    invalidation_price: float,
    tolerance: float,
) -> ImmediateRebalance:
    rebalance_time = pd.Timestamp(c3["time_open"])
    return ImmediateRebalance(
        direction=direction,
        timeframe=timeframe,
        origin_time=pd.Timestamp(c1["time_open"]),
        impulse_time=pd.Timestamp(c2["time_open"]),
        rebalance_time=rebalance_time,
        available_time=rebalance_time + delta,
        origin_price=origin_price,
        rebalance_price=rebalance_price,
        impulse_body_low=impulse_body_low,
        impulse_body_high=impulse_body_high,
        impulse_body_size=impulse_body_size,
        invalidation_price=invalidation_price,
        tolerance=tolerance,
        metadata={
            "index": idx,
            "origin_open": float(c1["open"]),
            "origin_high": float(c1["high"]),
            "origin_low": float(c1["low"]),
            "origin_close": float(c1["close"]),
            "impulse_open": float(c2["open"]),
            "impulse_high": float(c2["high"]),
            "impulse_low": float(c2["low"]),
            "impulse_close": float(c2["close"]),
            "rebalance_open": float(c3["open"]),
            "rebalance_high": float(c3["high"]),
            "rebalance_low": float(c3["low"]),
            "rebalance_close": float(c3["close"]),
        },
    )


def immediate_rebalance_failed(ir: ImmediateRebalance, candle: pd.Series | dict[str, float]) -> bool:
    close = float(candle["close"])
    if ir.direction == "bullish":
        return close < ir.invalidation_price
    return close > ir.invalidation_price


def immediate_rebalance_extension_confirmed(
    ir: ImmediateRebalance,
    candles: pd.DataFrame,
    extension_candles: int = 2,
    min_body_ratio: float = 0.5,
) -> bool:
    """Post-entry validation: the next candles extend in the IR direction."""

    df = normalize_candles(candles)
    after = df[pd.to_datetime(df["time_open"]) > ir.rebalance_time].head(extension_candles)
    if len(after) < extension_candles:
        return False
    for _, row in after.iterrows():
        if _candle_body_ratio(row) < min_body_ratio:
            return False
        if ir.direction == "bullish" and float(row["close"]) <= float(row["open"]):
            return False
        if ir.direction == "bearish" and float(row["close"]) >= float(row["open"]):
            return False
    return True


def detect_session_ranges(
    candles: pd.DataFrame,
    session_name: str = "asian",
    timezone_name: str = "America/New_York",
    start: str | None = None,
    end: str | None = None,
) -> list[SessionRange]:
    df = normalize_candles(candles)
    if df.empty:
        return []
    name = session_name.lower()
    start_value, end_value = (start, end) if start and end else SESSION_WINDOWS.get(name, SESSION_WINDOWS["asian"])
    start_minutes = _hhmm_to_minutes(start_value)
    end_minutes = _hhmm_to_minutes(end_value)

    work = df.copy()
    times_utc = pd.to_datetime(work["time_open"], utc=True)
    local_times = times_utc.dt.tz_convert(timezone_name)
    work["_time_utc"] = times_utc
    work["_time_local"] = local_times
    work["_local_minutes"] = local_times.dt.hour * 60 + local_times.dt.minute
    if end_minutes <= start_minutes:
        work["_session_date"] = local_times.dt.date
        early = work["_local_minutes"] < end_minutes
        work.loc[early, "_session_date"] = (local_times[early] - pd.Timedelta(days=1)).dt.date
        mask = (work["_local_minutes"] >= start_minutes) | (work["_local_minutes"] < end_minutes)
    else:
        work["_session_date"] = local_times.dt.date
        mask = (work["_local_minutes"] >= start_minutes) & (work["_local_minutes"] < end_minutes)
    sessions: list[SessionRange] = []
    for local_date, group in work[mask].groupby("_session_date"):
        if group.empty:
            continue
        local_start = pd.Timestamp(f"{local_date} {start_value}", tz=timezone_name)
        local_end_date = pd.Timestamp(local_date)
        if end_minutes <= start_minutes:
            local_end_date += pd.Timedelta(days=1)
        local_end = pd.Timestamp(f"{local_end_date.date()} {end_value}", tz=timezone_name)
        available_time = local_end.tz_convert("UTC")
        sessions.append(
            SessionRange(
                name=name,
                local_date=str(local_date),
                start_time=local_start.tz_convert("UTC"),
                end_time=available_time,
                available_time=available_time,
                high=float(group["high"].max()),
                low=float(group["low"].min()),
                metadata={"timezone": timezone_name, "start": start_value, "end": end_value, "rows": int(len(group))},
            )
        )
    return sorted(sessions, key=lambda item: item.available_time)


def latest_completed_session_range(
    candles: pd.DataFrame,
    current_time: pd.Timestamp,
    session_name: str,
    timezone_name: str = "America/New_York",
    start: str | None = None,
    end: str | None = None,
) -> SessionRange | None:
    current = current_time.tz_convert("UTC") if current_time.tzinfo else current_time.tz_localize("UTC")
    completed = [
        session
        for session in detect_session_ranges(candles, session_name, timezone_name, start, end)
        if session.available_time <= current
    ]
    return completed[-1] if completed else None


def detect_equal_high_lows(
    pivots: list[Pivot],
    tick_size: float = 0.25,
    tolerance_ticks: int = 2,
    min_touches: int = 2,
) -> list[EqualHighLow]:
    tolerance = max(float(tick_size) * int(tolerance_ticks), 0.0)
    levels: list[EqualHighLow] = []
    for kind in ("high", "low"):
        candidates = sorted([pivot for pivot in pivots if pivot.kind == kind], key=lambda pivot: pivot.confirmation_time)
        for index, pivot in enumerate(candidates):
            touches = [pivot]
            for other in candidates[index + 1 :]:
                if abs(other.price - pivot.price) <= tolerance:
                    touches.append(other)
            if len(touches) >= min_touches:
                price = sum(item.price for item in touches) / len(touches)
                levels.append(
                    EqualHighLow(
                        kind=kind,  # type: ignore[arg-type]
                        price=float(price),
                        first_time=touches[0].pivot_time,
                        second_time=touches[1].pivot_time,
                        available_time=max(item.confirmation_time for item in touches[:min_touches]),
                        tolerance=tolerance,
                        touches=len(touches),
                        metadata={"pivot_times": [item.pivot_time for item in touches], "prices": [item.price for item in touches]},
                    )
                )
    return sorted(levels, key=lambda item: item.available_time)


def detect_structure_breaks(candles: pd.DataFrame, pivots: list[Pivot], timeframe: str = "M1") -> list[StructureBreak]:
    df = normalize_candles(candles)
    if df.empty or not pivots:
        return []
    delta = timeframe_delta(timeframe)
    sorted_pivots = sorted(pivots, key=lambda pivot: pivot.confirmation_time)
    pivot_idx = 0
    known: list[Pivot] = []
    broken_highs: set[pd.Timestamp] = set()
    broken_lows: set[pd.Timestamp] = set()
    events: list[StructureBreak] = []
    for _, row in df.iterrows():
        candle_time = pd.Timestamp(row["time_open"])
        available_time = candle_time + delta
        while pivot_idx < len(sorted_pivots) and sorted_pivots[pivot_idx].confirmation_time <= candle_time:
            known.append(sorted_pivots[pivot_idx])
            pivot_idx += 1
        last_high = next((pivot for pivot in reversed(known) if pivot.kind == "high"), None)
        last_low = next((pivot for pivot in reversed(known) if pivot.kind == "low"), None)
        previous_trend = _swing_bias_from_pivots(known)
        close = float(row["close"])
        if last_high is not None and last_high.pivot_time not in broken_highs and close > last_high.price:
            broken_highs.add(last_high.pivot_time)
            events.append(
                StructureBreak(
                    kind="BOS" if previous_trend == "bullish" else "MSS",
                    direction="bullish",
                    timeframe=timeframe,
                    break_time=candle_time,
                    available_time=available_time,
                    level=last_high.price,
                    close=close,
                    pivot_time=last_high.pivot_time,
                    previous_trend=previous_trend,
                    metadata={"pivot_kind": "high"},
                )
            )
        if last_low is not None and last_low.pivot_time not in broken_lows and close < last_low.price:
            broken_lows.add(last_low.pivot_time)
            events.append(
                StructureBreak(
                    kind="BOS" if previous_trend == "bearish" else "MSS",
                    direction="bearish",
                    timeframe=timeframe,
                    break_time=candle_time,
                    available_time=available_time,
                    level=last_low.price,
                    close=close,
                    pivot_time=last_low.pivot_time,
                    previous_trend=previous_trend,
                    metadata={"pivot_kind": "low"},
                )
            )
    return sorted(events, key=lambda item: item.available_time)


def detect_amd_phases(
    candles: pd.DataFrame,
    timeframe: str = "M1",
    range_bars: int = 30,
    max_range_atr_multiplier: float = 1.4,
    displacement_multiplier: float = 1.1,
    min_displacement_body_ratio: float = 0.55,
) -> list[AmdPhase]:
    df = normalize_candles(candles)
    if len(df) < range_bars + 2:
        return []
    delta = timeframe_delta(timeframe)
    numeric = df.copy()
    for column in ["open", "high", "low", "close"]:
        numeric[column] = pd.to_numeric(numeric[column], errors="coerce")
    ranges = numeric["high"] - numeric["low"]
    phases: list[AmdPhase] = []
    for idx in range(range_bars + 1, len(numeric)):
        range_frame = numeric.iloc[idx - range_bars - 1 : idx - 1]
        sweep = numeric.iloc[idx - 1]
        displacement = numeric.iloc[idx]
        range_high = float(range_frame["high"].max())
        range_low = float(range_frame["low"].min())
        range_size = range_high - range_low
        median_bar_range = float(ranges.iloc[idx - range_bars - 1 : idx - 1].median() or 0)
        if median_bar_range <= 0 or range_size > median_bar_range * range_bars * max_range_atr_multiplier:
            continue
        body = abs(float(displacement["close"] - displacement["open"]))
        if _candle_body_ratio(displacement) < min_displacement_body_ratio or body < median_bar_range * displacement_multiplier:
            continue
        sweep_time = pd.Timestamp(sweep["time_open"])
        displacement_time = pd.Timestamp(displacement["time_open"])
        if float(sweep["low"]) < range_low and float(sweep["close"]) > range_low and float(displacement["close"]) > range_high:
            phases.append(
                AmdPhase(
                    phase="accumulation_candidate",
                    direction="bullish",
                    timeframe=timeframe,
                    range_start=pd.Timestamp(range_frame.iloc[0]["time_open"]),
                    range_end=pd.Timestamp(range_frame.iloc[-1]["time_open"]),
                    available_time=displacement_time + delta,
                    range_high=range_high,
                    range_low=range_low,
                    sweep_time=sweep_time,
                    displacement_time=displacement_time,
                    metadata={"sweep": "low", "range_bars": range_bars},
                )
            )
        if float(sweep["high"]) > range_high and float(sweep["close"]) < range_high and float(displacement["close"]) < range_low:
            phases.append(
                AmdPhase(
                    phase="distribution_candidate",
                    direction="bearish",
                    timeframe=timeframe,
                    range_start=pd.Timestamp(range_frame.iloc[0]["time_open"]),
                    range_end=pd.Timestamp(range_frame.iloc[-1]["time_open"]),
                    available_time=displacement_time + delta,
                    range_high=range_high,
                    range_low=range_low,
                    sweep_time=sweep_time,
                    displacement_time=displacement_time,
                    metadata={"sweep": "high", "range_bars": range_bars},
                )
            )
    return sorted(phases, key=lambda item: item.available_time)


def _candle_body_ratio(row: pd.Series) -> float:
    candle_range = float(row["high"] - row["low"])
    if candle_range <= 0:
        return 0.0
    return abs(float(row["close"] - row["open"])) / candle_range


def _hhmm_to_minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _swing_bias_from_pivots(pivots: list[Pivot]) -> str:
    highs = [pivot for pivot in pivots if pivot.kind == "high"][-2:]
    lows = [pivot for pivot in pivots if pivot.kind == "low"][-2:]
    if len(highs) < 2 or len(lows) < 2:
        return "neutral"
    if highs[-1].price > highs[-2].price and lows[-1].price > lows[-2].price:
        return "bullish"
    if highs[-1].price < highs[-2].price and lows[-1].price < lows[-2].price:
        return "bearish"
    return "neutral"


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
