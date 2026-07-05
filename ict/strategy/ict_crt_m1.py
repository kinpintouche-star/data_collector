from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ict.data.resample import build_timeframes
from ict.strategy.indicators import Pivot, crt_signal, detect_pivots
from ict.strategy.params import StrategyParams
from ict.strategy.pd_arrays import detect_fvgs, detect_order_blocks


@dataclass
class PreparedMarketData:
    m1: pd.DataFrame
    m15: pd.DataFrame
    h1: pd.DataFrame
    m15_pivots: list[Pivot]


def prepare_market_data(m1: pd.DataFrame) -> PreparedMarketData:
    frames = build_timeframes(m1, ("M15", "H1"))
    return PreparedMarketData(
        m1=m1,
        m15=frames["M15"],
        h1=frames["H1"],
        m15_pivots=detect_pivots(frames["M15"], "M15"),
    )


def scan_h1_signals(h1: pd.DataFrame, params: StrategyParams) -> list[tuple[pd.Timestamp, object]]:
    signals: list[tuple[pd.Timestamp, object]] = []
    if len(h1) < 2:
        return signals
    for idx in range(1, len(h1)):
        c1 = h1.iloc[idx - 1].to_dict()
        c2 = h1.iloc[idx].to_dict()
        signal = crt_signal(c1, c2, detect_c3=params.detect_c3, model=params.crt_model)
        if signal is not None:
            signals.append((pd.Timestamp(h1.iloc[idx]["time_open"]), signal))
    return signals


def scan_pd_arrays(m1: pd.DataFrame, params: StrategyParams):
    return {
        "fvgs": detect_fvgs(m1),
        "obs": detect_order_blocks(
            m1,
            sensitivity_mode=params.ob_sensitivity_mode,
            atr_len=params.ob_atr_len,
            ob1_sensitivity=params.ob1_sensitivity,
            ob2_sensitivity=params.ob2_sensitivity,
            ob_min_body_ratio=params.ob_min_body_ratio,
            ob_lookback1=params.ob_lookback1,
            ob_lookback2_from=params.ob_lookback2_from,
            ob_lookback2_to=params.ob_lookback2_to,
        ),
    }
