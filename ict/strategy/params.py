from __future__ import annotations

from typing import Literal

import yaml
from pydantic import BaseModel, Field


class Killzone(BaseModel):
    start_hour: int
    end_hour: int


class ExecutionParams(BaseModel):
    initial_balance: float = 100000
    order_qty: float = 1.0
    fill_policy: Literal["signal_close", "next_open"] = "signal_close"
    ambiguous_bar_policy: Literal["sl_first", "tp_first", "ohlc_path"] = "sl_first"
    close_open_on_run_end: bool = True
    commission_per_trade: float = 0.0
    slippage_ticks: int = 0
    max_one_position_per_symbol: bool = True


class StrategyParams(BaseModel):
    timezone: str = "America/New_York"
    only_killzone: bool = False
    killzones: dict[str, Killzone] = Field(
        default_factory=lambda: {
            "london": Killzone(start_hour=1, end_hour=5),
            "new_york": Killzone(start_hour=7, end_hour=11),
        }
    )
    detect_c3: bool = True
    trade_direction: Literal["auto", "bullish_only", "bearish_only"] = "auto"
    strategy_mode: Literal[
        "A_INVALIDATION_S2",
        "B_NO_S2_INVALIDATION",
        "C_S2_INSIDE_S1",
    ] = "B_NO_S2_INVALIDATION"
    asset_type: str = "Index"
    crt_model: Literal["sweep_back_in", "body_inside"] = "sweep_back_in"
    ote_deep: float = 0.79
    fib_forward_minutes: int = 180
    pd_mode: Literal["OB_SOLID", "FVG", "OB_OR_FVG"] = "OB_OR_FVG"
    pd_require_mid_in_ote: bool = True
    sl_buffer_ticks: int = 2
    ob_sensitivity_mode: Literal["ATR_ADAPTIVE", "PERCENT"] = "ATR_ADAPTIVE"
    ob_atr_len: int = 14
    ob1_sensitivity: float = 5.0
    ob2_sensitivity: float = 8.0
    ob_min_body_ratio: float = 0.55
    ob_lookback1: int = 10
    ob_lookback2_from: int = 4
    ob_lookback2_to: int = 15
    execution: ExecutionParams = Field(default_factory=ExecutionParams)


def load_strategy_params(path: str) -> StrategyParams:
    with open(path, "r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return StrategyParams.model_validate(payload)
