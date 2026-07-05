from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class SimulatedTrade:
    direction: Literal["bullish", "bearish"]
    entry_price: float
    sl: float
    tp: float
    volume: float


def close_reason_for_bar(
    trade: SimulatedTrade,
    high: float,
    low: float,
    ambiguous_bar_policy: Literal["sl_first", "tp_first", "ohlc_path"] = "sl_first",
) -> str | None:
    if trade.direction == "bullish":
        hit_sl = low <= trade.sl
        hit_tp = high >= trade.tp
    else:
        hit_sl = high >= trade.sl
        hit_tp = low <= trade.tp

    if hit_sl and hit_tp:
        return "TP" if ambiguous_bar_policy == "tp_first" else "SL"
    if hit_sl:
        return "SL"
    if hit_tp:
        return "TP"
    return None


def pnl_points(direction: str, entry_price: float, exit_price: float) -> float:
    if direction == "bullish":
        return exit_price - entry_price
    if direction == "bearish":
        return entry_price - exit_price
    raise ValueError(f"Unsupported direction: {direction}")
