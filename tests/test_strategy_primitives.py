from __future__ import annotations

import pandas as pd

from ict.strategy.indicators import (
    crt_signal,
    detect_amd_phases,
    detect_equal_high_lows,
    detect_immediate_rebalances,
    fib_level,
    detect_session_ranges,
    detect_structure_breaks,
    immediate_rebalance_extension_confirmed,
    immediate_rebalance_failed,
    ote_zone,
    pd_touched,
    rejection_confirmed,
    risk_is_valid,
    Pivot,
    s2_inside_rule,
    s2_invalidated,
)
from ict.strategy.pd_arrays import detect_fvgs, detect_order_blocks, select_pd_array


def test_ote_calculation_bullish_bearish() -> None:
    assert fib_level(100, 120, 0.5) == 110
    assert fib_level(120, 100, 0.5) == 110
    assert ote_zone(100, 120, 0.79) == (104.2, 107.64)
    assert ote_zone(120, 100, 0.79) == (112.36, 115.8)


def test_s2_inside_rule_and_invalidation() -> None:
    assert s2_inside_rule("bearish", 110, 105)
    assert s2_inside_rule("bullish", 90, 95)
    assert s2_invalidated("bearish", close=106, s2_price=105)
    assert s2_invalidated("bullish", close=94, s2_price=95)


def test_fvg_detection_bullish_bearish() -> None:
    candles = pd.DataFrame(
        {
            "time_open": pd.date_range("2025-01-01", periods=6, freq="min", tz="UTC"),
            "open": [10, 11, 13, 13, 10, 8],
            "high": [11, 12, 14, 14, 11, 9],
            "low": [9, 10, 12, 12, 9, 7],
            "close": [10.5, 11.5, 13.5, 13, 10, 8],
        }
    )

    zones = detect_fvgs(candles)

    assert any(zone.direction == "bullish" and zone.bottom == 11 and zone.top == 12 for zone in zones)
    assert any(zone.direction == "bearish" and zone.bottom == 9 and zone.top == 12 for zone in zones)


def test_ob_detection_percent_mode() -> None:
    candles = pd.DataFrame(
        {
            "time_open": pd.date_range("2025-01-01", periods=6, freq="min", tz="UTC"),
            "open": [100, 101, 100, 99, 98, 101],
            "high": [101, 102, 101, 100, 99, 108],
            "low": [99, 100, 98, 96, 95, 100],
            "close": [100.5, 101.5, 99, 97, 96, 107],
        }
    )

    zones = detect_order_blocks(
        candles,
        sensitivity_mode="PERCENT",
        ob1_sensitivity=1.0,
        ob_min_body_ratio=0.4,
        ob_lookback1=4,
    )

    assert any(zone.kind == "OB" and zone.direction == "bullish" for zone in zones)


def test_ob_detection_atr_mode() -> None:
    candles = pd.DataFrame(
        {
            "time_open": pd.date_range("2025-01-01", periods=8, freq="min", tz="UTC"),
            "open": [100, 100, 99, 98, 97, 96, 100, 105],
            "high": [101, 101, 100, 99, 98, 97, 105, 111],
            "low": [99, 99, 97, 96, 95, 94, 99, 104],
            "close": [100, 99, 98, 97, 96, 95, 104, 110],
        }
    )

    zones = detect_order_blocks(
        candles,
        sensitivity_mode="ATR_ADAPTIVE",
        atr_len=3,
        ob1_sensitivity=1.0,
        ob_min_body_ratio=0.3,
        ob_lookback1=5,
    )

    assert any(zone.direction == "bullish" for zone in zones)


def test_pd_selection_midpoint_in_ote() -> None:
    candles = pd.DataFrame(
        {
            "time_open": pd.date_range("2025-01-01", periods=3, freq="min", tz="UTC"),
            "open": [100, 101, 105],
            "high": [101, 102, 106],
            "low": [99, 100, 103],
            "close": [100, 101, 105],
        }
    )
    fvg = detect_fvgs(candles)[0]
    selected = select_pd_array(
        obs=[],
        fvgs=[fvg],
        direction="bullish",
        s2_time=pd.Timestamp("2025-01-01 00:00", tz="UTC"),
        leg_end_time=pd.Timestamp("2025-01-01 00:02", tz="UTC"),
        ote_bottom=101,
        ote_top=104,
        pd_mode="FVG",
        require_midpoint=True,
    )

    assert selected == fvg


def test_pd_selection_prefers_latest_matching_zone() -> None:
    times = pd.date_range("2025-01-01", periods=4, freq="min", tz="UTC")
    candles = pd.DataFrame(
        {
            "time_open": times,
            "open": [100, 101, 105, 106],
            "high": [101, 102, 106, 107],
            "low": [99, 100, 103, 104],
            "close": [100, 101, 105, 106],
        }
    )
    first, latest = detect_fvgs(candles)

    selected = select_pd_array(
        obs=[],
        fvgs=[first, latest],
        direction="bullish",
        s2_time=times[0],
        leg_end_time=times[-1],
        ote_bottom=101,
        ote_top=105,
        pd_mode="FVG",
        require_midpoint=False,
    )

    assert selected == latest


def test_rejection_logic_and_risk_validation() -> None:
    candle = {"open": 100, "high": 105, "low": 98, "close": 104}
    assert pd_touched(candle, pd_bottom=99, pd_top=103)
    assert rejection_confirmed("bullish", candle, pd_mid=102, pd_mitigated=True)
    assert risk_is_valid("bullish", entry_price=104, sl=98, tp=110)
    assert risk_is_valid("bearish", entry_price=100, sl=105, tp=90)


def test_crt_sweep_back_in_signal() -> None:
    c1 = {"open": 100, "high": 110, "low": 90, "close": 100}
    c2 = {"open": 100, "high": 111, "low": 95, "close": 99}

    signal = crt_signal(c1, c2)

    assert signal is not None
    assert signal.direction == "bearish"


def test_immediate_rebalance_detection_and_invalidation() -> None:
    candles = pd.DataFrame(
        {
            "time_open": pd.date_range("2025-01-01", periods=7, freq="min", tz="UTC"),
            "open": [100.0, 101.0, 103.0, 110.0, 109.0, 106.0, 104.0],
            "high": [102.0, 109.0, 104.0, 111.0, 110.0, 111.0, 105.0],
            "low": [99.0, 100.0, 99.0, 100.0, 101.0, 103.0, 102.0],
            "close": [101.0, 108.0, 103.8, 109.0, 102.0, 104.0, 103.0],
        }
    )

    bullish, bearish = detect_immediate_rebalances(
        candles,
        timeframe="M1",
        tick_size=0.1,
        tolerance_ticks=1,
        min_impulse_body_ticks=10,
    )

    assert bullish.direction == "bullish"
    assert bullish.origin_price == 99.0
    assert bullish.rebalance_price == 99.0
    assert bullish.invalidation_price == 101.0
    assert bullish.available_time == pd.Timestamp("2025-01-01 00:03", tz="UTC")
    assert immediate_rebalance_failed(bullish, {"close": 100.9})
    bullish_extension = pd.DataFrame(
        {
            "time_open": pd.date_range("2025-01-01 00:02", periods=3, freq="min", tz="UTC"),
            "open": [103.0, 104.0, 106.0],
            "high": [104.0, 107.0, 109.0],
            "low": [99.0, 103.8, 105.8],
            "close": [103.8, 106.5, 108.5],
        }
    )
    assert immediate_rebalance_extension_confirmed(bullish, bullish_extension, extension_candles=2, min_body_ratio=0.2)

    assert bearish.direction == "bearish"
    assert bearish.origin_price == 111.0
    assert bearish.rebalance_price == 111.0
    assert bearish.invalidation_price == 109.0
    assert immediate_rebalance_failed(bearish, {"close": 109.1})


def test_session_range_crosses_midnight_without_future_leakage() -> None:
    candles = pd.DataFrame(
        {
            "time_open": pd.date_range("2025-01-01 21:00", periods=300, freq="min", tz="UTC"),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
        }
    )
    candles.loc[candles["time_open"] == pd.Timestamp("2025-01-01 23:15", tz="UTC"), "high"] = 110.0
    candles.loc[candles["time_open"] == pd.Timestamp("2025-01-02 00:30", tz="UTC"), "low"] = 92.0

    sessions = detect_session_ranges(candles, "asian", timezone_name="UTC", start="22:00", end="01:00")

    assert len(sessions) == 1
    assert sessions[0].high == 110.0
    assert sessions[0].low == 92.0
    assert sessions[0].available_time == pd.Timestamp("2025-01-02 01:00", tz="UTC")


def test_equal_high_lows_with_tick_tolerance() -> None:
    base = pd.Timestamp("2025-01-01", tz="UTC")
    pivots = [
        Pivot("high", base, base + pd.Timedelta(minutes=1), 100.0, 99.0, 98.0),
        Pivot("high", base + pd.Timedelta(minutes=10), base + pd.Timedelta(minutes=11), 100.4, 99.0, 98.0),
        Pivot("low", base + pd.Timedelta(minutes=20), base + pd.Timedelta(minutes=21), 90.0, 91.0, 92.0),
    ]

    levels = detect_equal_high_lows(pivots, tick_size=0.25, tolerance_ticks=2)

    assert len(levels) == 1
    assert levels[0].kind == "high"
    assert levels[0].touches == 2
    assert levels[0].available_time == base + pd.Timedelta(minutes=11)


def test_structure_break_detects_bos_after_confirmed_swings() -> None:
    times = pd.date_range("2025-01-01", periods=8, freq="min", tz="UTC")
    candles = pd.DataFrame(
        {
            "time_open": times,
            "open": [100, 101, 102, 103, 104, 105, 110, 111],
            "high": [101, 103, 104, 105, 106, 109, 112, 113],
            "low": [99, 100, 101, 102, 103, 104, 109, 110],
            "close": [100, 102, 103, 104, 105, 108, 111, 112],
        }
    )
    pivots = [
        Pivot("high", times[1], times[2], 103.0, 101.0, 102.0),
        Pivot("low", times[2], times[3], 100.0, 101.0, 102.0),
        Pivot("high", times[4], times[5], 109.0, 106.0, 108.0),
        Pivot("low", times[5], times[6], 104.0, 105.0, 106.0),
    ]

    breaks = detect_structure_breaks(candles, pivots, timeframe="M1")

    assert any(item.kind == "BOS" and item.direction == "bullish" and item.level == 109.0 for item in breaks)


def test_amd_phase_detects_range_sweep_displacement() -> None:
    candles = pd.DataFrame(
        {
            "time_open": pd.date_range("2025-01-01", periods=6, freq="min", tz="UTC"),
            "open": [100.0, 100.2, 99.8, 100.1, 99.4, 101.0],
            "high": [101.0, 100.8, 100.6, 100.9, 100.2, 103.2],
            "low": [99.0, 99.3, 99.2, 99.1, 98.5, 100.9],
            "close": [100.2, 99.9, 100.1, 100.0, 99.5, 103.0],
        }
    )

    phases = detect_amd_phases(candles, timeframe="M1", range_bars=4, displacement_multiplier=0.8)

    assert len(phases) == 1
    assert phases[0].phase == "accumulation_candidate"
    assert phases[0].direction == "bullish"
    assert phases[0].available_time == pd.Timestamp("2025-01-01 00:06", tz="UTC")
