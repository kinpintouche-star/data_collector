from __future__ import annotations

import pandas as pd

from ict.api.backtests import BacktestLaunchRequest, create_backtest_job, get_backtest_job
from ict.strategy.blueprint_engine import BlueprintState, StrategyBlueprintEngine
from ict.strategy.builder import StrategyDefinitionPayload, strategy_templates, validation_result
from ict.strategy.indicators import Pivot


def _setbar(df: pd.DataFrame, timestamp: str, **values: float) -> None:
    idx = df.index[df["time_open"] == pd.Timestamp(timestamp, tz="UTC")][0]
    for key, value in values.items():
        df.at[idx, key] = value


def test_strategy_builder_template_validates() -> None:
    template = next(item for item in strategy_templates() if item["id"] == "ict_crt_m1_liquidity_confluence_v0")
    definition = StrategyDefinitionPayload.model_validate(template["definition"])
    result = validation_result(definition)

    assert result.valid
    assert result.definition_hash


def test_strategy_builder_crt_h1_m1_template_validates() -> None:
    template = next(item for item in strategy_templates() if item["id"] == "crt_h1_m1")
    definition = StrategyDefinitionPayload.model_validate(template["definition"])
    result = validation_result(definition)

    assert template["name"] == "CRT H1 M1"
    assert result.valid
    assert result.definition_hash


def test_strategy_builder_immediate_rebalance_template_validates() -> None:
    template = next(item for item in strategy_templates() if item["id"] == "immediate_rebalance_h1_m15_m1")
    definition = StrategyDefinitionPayload.model_validate(template["definition"])
    result = validation_result(definition)

    assert result.valid
    assert result.definition_hash
    assert any(block.type == "trigger.immediate_rebalance" for block in definition.blocks)
    assert any(block.type == "filter.trend" for block in definition.blocks)


def test_strategy_builder_experimental_templates_validate() -> None:
    experimental_ids = {
        "ir_liquidity_targets_experimental",
        "trend_aligned_crt_experimental",
        "amd_range_sweep_experimental",
    }

    for template in strategy_templates():
        if template["id"] not in experimental_ids:
            continue
        definition = StrategyDefinitionPayload.model_validate(template["definition"])
        result = validation_result(definition)
        assert result.valid, result.errors


def test_strategy_builder_rejects_unknown_block_and_missing_reference() -> None:
    payload = {
        "global_params": {},
        "timeframes": ["M1"],
        "blocks": [
            {"id": "bad", "type": "trigger.unknown", "timeframe": "M1", "params": {}, "outputs": []},
            {
                "id": "fib",
                "type": "compute.fibonacci",
                "timeframe": "M1",
                "params": {"start_ref": "missing.output"},
                "outputs": ["fib"],
            },
            {
                "id": "order",
                "type": "action.order",
                "timeframe": "M1",
                "params": {"take_profit": "crt_objective", "stop_loss": "structural_pd_array", "min_rr": 2},
                "outputs": ["order"],
            },
        ],
        "execution": {},
    }

    result = validation_result(payload)

    assert not result.valid
    assert any("unknown type" in item for item in result.errors)
    assert any("missing.output" in item for item in result.errors)


def test_blueprint_engine_current_clone_produces_trade_and_block_metadata() -> None:
    template = next(item for item in strategy_templates() if item["id"] == "crt_h1_m1")
    result = StrategyBlueprintEngine(template["definition"], tick_size=0.25).run(_sample_trade_frame())
    event_types = [event["event_type"] for event in result.events]

    assert len(result.trades) == 1
    assert len(result.orders) == 1
    assert len(result.fills) == 1
    assert "CRT_SIGNAL" in event_types
    assert "LEG_FOUND" in event_types
    assert "TRADE_OPENED" in event_types
    assert result.trades.iloc[0]["metadata"]["strategy_builder"] is True
    assert result.trades.iloc[0]["metadata"]["swing_block"] == "m15_swings"


def test_blueprint_engine_period_liquidity_targets() -> None:
    template = next(item for item in strategy_templates() if item["id"] == "immediate_rebalance_h1_m15_m1")
    engine = StrategyBlueprintEngine(template["definition"], tick_size=0.25)
    candles = pd.DataFrame(
        {
            "time_open": pd.to_datetime(
                [
                    "2025-12-15 00:00",
                    "2025-12-20 00:00",
                    "2026-01-05 00:00",
                    "2026-01-08 00:00",
                    "2026-01-12 00:00",
                ],
                utc=True,
            ),
            "open": [100, 100, 100, 100, 100],
            "high": [130, 128, 120, 126, 110],
            "low": [80, 82, 90, 88, 95],
            "close": [100, 100, 100, 100, 100],
        }
    )

    current_time = pd.Timestamp("2026-01-12 12:00", tz="UTC")

    assert engine._previous_period_level("bullish", current_time, candles, "week") == {
        "price": 126.0,
        "source": "previous_week_high",
    }
    assert engine._previous_period_level("bearish", current_time, candles, "month") == {
        "price": 80.0,
        "source": "previous_month_low",
    }


def test_blueprint_engine_trend_filter_follow_parent() -> None:
    template = next(item for item in strategy_templates() if item["id"] == "immediate_rebalance_h1_m15_m1")
    engine = StrategyBlueprintEngine(template["definition"], tick_size=0.25)
    current_time = pd.Timestamp("2026-01-02 12:00", tz="UTC")
    state = BlueprintState(
        known_pivots={
            "H1": _trend_pivots("bullish", current_time),
            "M15": _trend_pivots("bullish", current_time),
        }
    )

    allowed, metadata = engine._trend_allows("bullish", state, current_time)

    assert allowed
    assert metadata["trends"][0]["trend"] == "bullish"


def test_backtest_job_accepts_strategy_definition_id() -> None:
    request = BacktestLaunchRequest.model_validate(
        {
            "strategy_definition_id": "00000000-0000-0000-0000-000000000001",
            "assets": [{"symbol_code": "EURUSD", "source_name": "dukascopy"}],
            "from": "2026-01-01",
            "to": "2026-01-02",
            "timeframe": "m1",
        }
    )

    job = create_backtest_job(request)
    stored = get_backtest_job(job.id)

    assert stored.status == "queued"
    assert stored.launch_id == job.launch_id
    assert stored.total_assets == 1


def _sample_trade_frame() -> pd.DataFrame:
    times = pd.date_range("2025-01-01 00:00", periods=240, freq="min", tz="UTC")
    m1 = pd.DataFrame(
        {
            "time_open": times,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
        }
    )

    _setbar(m1, "2025-01-01 00:10", high=110)
    _setbar(m1, "2025-01-01 00:20", low=90)
    _setbar(m1, "2025-01-01 00:59", close=100)
    _setbar(m1, "2025-01-01 01:05", high=112)
    _setbar(m1, "2025-01-01 01:30", high=109)
    _setbar(m1, "2025-01-01 01:59", close=99)
    _setbar(m1, "2025-01-01 02:00", low=90)
    _setbar(m1, "2025-01-01 02:15", low=80)
    _setbar(m1, "2025-01-01 02:30", low=88)
    _setbar(m1, "2025-01-01 02:08", open=104, high=104, low=103, close=103.5)
    _setbar(m1, "2025-01-01 02:10", open=98, high=99, low=96, close=97)
    _setbar(m1, "2025-01-01 02:50", open=104, high=104, low=100, close=101)
    _setbar(m1, "2025-01-01 02:55", open=100, high=101, low=89, close=90)
    return m1


def _trend_pivots(direction: str, current_time: pd.Timestamp) -> list[Pivot]:
    if direction == "bullish":
        highs = [100, 110]
        lows = [90, 95]
    else:
        highs = [110, 100]
        lows = [95, 90]
    return [
        Pivot("high", current_time - pd.Timedelta(hours=6), current_time - pd.Timedelta(hours=5), highs[0], highs[0] - 1, highs[0] - 2),
        Pivot("low", current_time - pd.Timedelta(hours=5), current_time - pd.Timedelta(hours=4), lows[0], lows[0] + 1, lows[0] + 2),
        Pivot("high", current_time - pd.Timedelta(hours=3), current_time - pd.Timedelta(hours=2), highs[1], highs[1] - 1, highs[1] - 2),
        Pivot("low", current_time - pd.Timedelta(hours=2), current_time - pd.Timedelta(hours=1), lows[1], lows[1] + 1, lows[1] + 2),
    ]
