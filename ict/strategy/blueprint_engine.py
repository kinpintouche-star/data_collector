from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd

from ict.backtest.broker_sim import SimulatedTrade, close_reason_for_bar, pnl_points
from ict.backtest.engine import ActiveSetup, BacktestEngine, BacktestResult, OpenTrade, PendingEntry
from ict.backtest.metrics import summarize_trades
from ict.data.candles import normalize_candles, timeframe_delta
from ict.data.gaps import CandleGapPlan, split_continuous_candles
from ict.data.resample import build_timeframes
from ict.strategy.builder import StrategyBlock, StrategyDefinitionPayload
from ict.strategy.indicators import (
    AmdPhase,
    ImmediateRebalance,
    Pivot,
    StructureBreak,
    crt_signal,
    detect_amd_phases,
    detect_equal_high_lows,
    detect_immediate_rebalances,
    detect_pivots,
    detect_structure_breaks,
    latest_completed_session_range,
    ote_zone,
    pd_touched,
    rejection_confirmed,
    risk_is_valid,
)
from ict.strategy.params import ExecutionParams, StrategyParams
from ict.strategy.pd_arrays import PriceZone, detect_fvgs, detect_order_blocks, select_pd_array


@dataclass(frozen=True)
class BlueprintRuntimeConfig:
    crt_block: StrategyBlock
    swing_block: StrategyBlock | None
    leg_block: StrategyBlock | None
    target_block: StrategyBlock | None
    fib_block: StrategyBlock | None
    retracement_block: StrategyBlock | None
    ir_block: StrategyBlock | None
    structure_block: StrategyBlock | None
    session_block: StrategyBlock | None
    amd_block: StrategyBlock | None
    order_block: StrategyBlock
    bias_block: StrategyBlock | None
    trend_blocks: tuple[StrategyBlock, ...]
    confluence_block: StrategyBlock | None
    timeframes: tuple[str, ...]


@dataclass(frozen=True)
class BlueprintCrtEvent:
    available_time: pd.Timestamp
    source_time: pd.Timestamp
    timeframe: str
    direction: Literal["bullish", "bearish"]
    c1_high: float
    c1_low: float
    c1_mid: float
    c2_close: float
    is_c3: bool


@dataclass
class BlueprintMarketData:
    m1: pd.DataFrame
    frames: dict[str, pd.DataFrame]
    pivots: dict[str, list[Pivot]]
    fvgs: dict[str, list[PriceZone]]
    obs: dict[str, list[PriceZone]]
    irs: dict[str, list[ImmediateRebalance]]
    structure_breaks: dict[str, list[StructureBreak]]
    amd_phases: dict[str, list[AmdPhase]]


@dataclass
class BlueprintState:
    known_pivots: dict[str, list[Pivot]] = field(default_factory=dict)
    pivot_index: dict[str, int] = field(default_factory=dict)
    known_fvgs: dict[str, list[PriceZone]] = field(default_factory=dict)
    fvg_index: dict[str, int] = field(default_factory=dict)
    known_obs: dict[str, list[PriceZone]] = field(default_factory=dict)
    ob_index: dict[str, int] = field(default_factory=dict)
    known_irs: dict[str, list[ImmediateRebalance]] = field(default_factory=dict)
    ir_index: dict[str, int] = field(default_factory=dict)
    known_structure_breaks: dict[str, list[StructureBreak]] = field(default_factory=dict)
    structure_break_index: dict[str, int] = field(default_factory=dict)
    known_amd_phases: dict[str, list[AmdPhase]] = field(default_factory=dict)
    amd_phase_index: dict[str, int] = field(default_factory=dict)


class StrategyBlueprintEngine(BacktestEngine):
    """Block-orchestrated ICT/SMC strategy engine.

    The legacy BacktestEngine remains available for the Pine-port strategy. This engine compiles a
    Strategy Builder definition into an event-driven pipeline where each block owns part of the
    setup lifecycle and records metadata for later review.
    """

    def __init__(self, definition: StrategyDefinitionPayload | dict[str, Any], tick_size: float = 0.25):
        self.definition = (
            definition if isinstance(definition, StrategyDefinitionPayload) else StrategyDefinitionPayload.model_validate(definition)
        )
        self.runtime = self._compile_runtime(self.definition)
        params = self._params_from_definition(self.definition, self.runtime)
        super().__init__(params, tick_size=tick_size)

    def run(self, m1_candles: pd.DataFrame) -> BacktestResult:
        m1 = normalize_candles(m1_candles)
        if m1.empty:
            trades = pd.DataFrame()
            return BacktestResult(trades=trades, metrics=summarize_trades(trades))

        gap_plan = split_continuous_candles(m1, "M1", min_segment_rows=self.gap_min_segment_rows)
        if gap_plan.gaps:
            return self._run_gap_segments(gap_plan)

        market = self._prepare_market_data(m1)
        crt_events = self._crt_events(market.frames[self.runtime.crt_block.timeframe or "H1"])

        events: list[dict] = []
        order_rows: list[dict] = []
        fill_rows: list[dict] = []
        trade_rows: list[dict] = []
        equity_rows: list[dict] = []
        state = BlueprintState(
            known_pivots={timeframe: [] for timeframe in market.pivots},
            pivot_index={timeframe: 0 for timeframe in market.pivots},
            known_fvgs={timeframe: [] for timeframe in market.fvgs},
            fvg_index={timeframe: 0 for timeframe in market.fvgs},
            known_obs={timeframe: [] for timeframe in market.obs},
            ob_index={timeframe: 0 for timeframe in market.obs},
            known_irs={timeframe: [] for timeframe in market.irs},
            ir_index={timeframe: 0 for timeframe in market.irs},
            known_structure_breaks={timeframe: [] for timeframe in market.structure_breaks},
            structure_break_index={timeframe: 0 for timeframe in market.structure_breaks},
            known_amd_phases={timeframe: [] for timeframe in market.amd_phases},
            amd_phase_index={timeframe: 0 for timeframe in market.amd_phases},
        )

        active: ActiveSetup | None = None
        open_trade: OpenTrade | None = None
        pending_entry: PendingEntry | None = None
        balance = float(self.params.execution.initial_balance)
        peak_equity = balance
        setup_counter = 0
        crt_idx = 0

        for bar_index, row in m1.iterrows():
            current_time = pd.Timestamp(row["time_open"])
            new_pivots, new_irs = self._advance_known_market_state(market, state, current_time, events)

            if pending_entry is not None and open_trade is None:
                fill_price = float(row["open"])
                if risk_is_valid(pending_entry.direction, fill_price, pending_entry.sl, pending_entry.tp):
                    open_trade = self._open_trade(
                        pending_entry,
                        current_time,
                        bar_index,
                        fill_price,
                        events,
                        order_rows,
                        fill_rows,
                    )
                    active = pending_entry.setup
                    active.state = "IN_POSITION"
                else:
                    self._set_order_status(order_rows, pending_entry.order_ref, "rejected")
                    events.append(
                        self._event(
                            "RISK_REJECTED",
                            current_time,
                            setup_id=pending_entry.setup.setup_id,
                            direction=pending_entry.direction,
                            price=fill_price,
                            state_before=pending_entry.setup.state,
                            state_after="INVALIDATED",
                            metadata={"fill_policy": "next_open", **pending_entry.metadata},
                        )
                    )
                    active = None
                pending_entry = None

            if open_trade is not None and bar_index > open_trade.entry_index:
                close_reason = close_reason_for_bar(
                    SimulatedTrade(
                        direction=open_trade.direction,
                        entry_price=open_trade.entry_price,
                        sl=open_trade.sl,
                        tp=open_trade.tp,
                        volume=open_trade.volume,
                    ),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    ambiguous_bar_policy=self.params.execution.ambiguous_bar_policy,
                )
                if close_reason is not None:
                    exit_price = open_trade.tp if close_reason == "TP" else open_trade.sl
                    trade_row = self._close_trade(open_trade, current_time, exit_price, close_reason)
                    trade_rows.append(trade_row)
                    balance += float(trade_row["pnl"])
                    events.append(
                        self._event(
                            "TRADE_CLOSED_TP" if close_reason == "TP" else "TRADE_CLOSED_SL",
                            current_time,
                            setup_id=open_trade.setup_id,
                            direction=open_trade.direction,
                            price=exit_price,
                            state_before="IN_POSITION",
                            state_after="COMPLETED",
                            metadata={"pnl": trade_row["pnl"], "rr": trade_row["rr"], **open_trade.metadata},
                        )
                    )
                    active = None
                    open_trade = None

            while crt_idx < len(crt_events) and crt_events[crt_idx].available_time <= current_time:
                signal = crt_events[crt_idx]
                crt_idx += 1
                if open_trade is not None or pending_entry is not None:
                    continue
                if not self._trade_direction_allowed(signal.direction):
                    continue
                if self.params.only_killzone and not self._in_killzone(signal.source_time):
                    continue
                if self.runtime.bias_block and not self._bias_allows(signal.direction, state, current_time):
                    events.append(
                        self._event(
                            "BLUEPRINT_BLOCK_REJECTED",
                            current_time,
                            setup_id="blueprint",
                            direction=signal.direction,
                            state_before="CRT_SIGNAL",
                            state_after="BIAS_REJECTED",
                            metadata={"block_id": self.runtime.bias_block.id, "block_type": self.runtime.bias_block.type},
                        )
                    )
                    continue
                trend_allowed, trend_metadata = self._trend_allows(signal.direction, state, current_time)
                if not trend_allowed:
                    events.append(
                        self._event(
                            "BLUEPRINT_BLOCK_REJECTED",
                            current_time,
                            setup_id="blueprint",
                            direction=signal.direction,
                            state_before="CRT_SIGNAL",
                            state_after="TREND_REJECTED",
                            metadata=trend_metadata,
                        )
                    )
                    continue
                structure_allowed, structure_metadata = self._structure_allows(signal.direction, state, current_time)
                if not structure_allowed:
                    events.append(
                        self._event(
                            "BLUEPRINT_BLOCK_REJECTED",
                            current_time,
                            setup_id="blueprint",
                            direction=signal.direction,
                            state_before="CRT_SIGNAL",
                            state_after="STRUCTURE_REJECTED",
                            metadata=structure_metadata,
                        )
                    )
                    continue
                amd_allowed, amd_metadata = self._amd_allows(signal.direction, state, current_time)
                if not amd_allowed:
                    events.append(
                        self._event(
                            "BLUEPRINT_BLOCK_REJECTED",
                            current_time,
                            setup_id="blueprint",
                            direction=signal.direction,
                            state_before="CRT_SIGNAL",
                            state_after="AMD_REJECTED",
                            metadata=amd_metadata,
                        )
                    )
                    continue

                setup_counter += 1
                setup_id = f"bp-setup-{setup_counter}"
                events.append(
                    self._event(
                        "CRT_SIGNAL",
                        current_time,
                        setup_id=setup_id,
                        direction=signal.direction,
                        price=signal.c2_close,
                        state_before="IDLE",
                        state_after="WAITING_SWINGS",
                        metadata={
                            "block_id": self.runtime.crt_block.id,
                            "timeframe": signal.timeframe,
                            "c1_high": signal.c1_high,
                            "c1_low": signal.c1_low,
                            "c2_close": signal.c2_close,
                            "is_c3": signal.is_c3,
                        },
                    )
                )
                if signal.timeframe == "H1":
                    events.append(
                        self._event(
                            "H1_SIGNAL",
                            current_time,
                            setup_id=setup_id,
                            direction=signal.direction,
                            price=signal.c2_close,
                            state_before="IDLE",
                            state_after="WAITING_SWINGS",
                            metadata={"source": "strategy_builder"},
                        )
                    )
                active = self._build_setup_from_signal(setup_id, signal, state, current_time, events)

            if active is not None and open_trade is None and pending_entry is None:
                active = self._advance_active_blueprint_setup(
                    active,
                    new_pivots,
                    new_irs,
                    state,
                    row,
                    current_time,
                    events,
                )
                if active is not None and active.state == "ORDER_PENDING":
                    pending_entry = self._pending_from_blueprint_setup(active, current_time, row, state, market.m1, events, order_rows)
                    if pending_entry is None:
                        active = None
                    elif self.params.execution.fill_policy == "signal_close":
                        open_trade = self._open_trade(
                            pending_entry,
                            current_time,
                            bar_index,
                            float(row["close"]),
                            events,
                            order_rows,
                            fill_rows,
                        )
                        active.state = "IN_POSITION"
                        pending_entry = None

            equity = balance
            if open_trade is not None:
                equity += pnl_points(open_trade.direction, open_trade.entry_price, float(row["close"])) * open_trade.volume
            peak_equity = max(peak_equity, equity)
            drawdown_abs = equity - peak_equity
            drawdown_pct = drawdown_abs / peak_equity if peak_equity else 0.0
            equity_rows.append(
                {
                    "time": current_time,
                    "balance": balance,
                    "equity": equity,
                    "drawdown_abs": drawdown_abs,
                    "drawdown_pct": drawdown_pct,
                    "open_positions": 1 if open_trade is not None else 0,
                }
            )

        if open_trade is not None and self.params.execution.close_open_on_run_end:
            last_row = m1.iloc[-1]
            exit_time = pd.Timestamp(last_row["time_open"])
            exit_price = float(last_row["close"])
            trade_row = self._close_trade(open_trade, exit_time, exit_price, "RUN_END")
            trade_rows.append(trade_row)
            balance += float(trade_row["pnl"])
            events.append(
                self._event(
                    "TRADE_CLOSED_RUN_END",
                    exit_time,
                    setup_id=open_trade.setup_id,
                    direction=open_trade.direction,
                    price=exit_price,
                    state_before="IN_POSITION",
                    state_after="COMPLETED",
                    metadata={"pnl": trade_row["pnl"], "rr": trade_row["rr"], **open_trade.metadata},
                )
            )
            if equity_rows:
                final_peak = max(peak_equity, balance)
                equity_rows[-1]["balance"] = balance
                equity_rows[-1]["equity"] = balance
                equity_rows[-1]["drawdown_abs"] = balance - final_peak
                equity_rows[-1]["drawdown_pct"] = (balance - final_peak) / final_peak if final_peak else 0.0
                equity_rows[-1]["open_positions"] = 0

        if pending_entry is not None and self.params.execution.close_open_on_run_end:
            last_row = m1.iloc[-1]
            exit_time = pd.Timestamp(last_row["time_open"])
            self._set_order_status(order_rows, pending_entry.order_ref, "cancelled")
            events.append(
                self._event(
                    "ORDER_CANCELLED_RUN_END",
                    exit_time,
                    setup_id=pending_entry.setup.setup_id,
                    direction=pending_entry.direction,
                    price=float(last_row["close"]),
                    state_before="ORDER_PENDING",
                    state_after="CANCELLED",
                    metadata=pending_entry.metadata,
                )
            )

        trades = pd.DataFrame(trade_rows)
        orders = pd.DataFrame(order_rows)
        fills = pd.DataFrame(fill_rows)
        equity_curve = pd.DataFrame(equity_rows)
        metrics = summarize_trades(trades)
        metrics.update(self._event_counts(events, equity_curve))
        metrics["strategy_builder"] = True
        return BacktestResult(events=events, orders=orders, fills=fills, trades=trades, equity_curve=equity_curve, metrics=metrics)

    def _run_gap_segments(self, gap_plan: CandleGapPlan) -> BacktestResult:
        return super()._run_gap_segments(gap_plan)

    def _compile_runtime(self, definition: StrategyDefinitionPayload) -> BlueprintRuntimeConfig:
        enabled = [block for block in definition.blocks if block.enabled]

        def first(block_type: str) -> StrategyBlock | None:
            return next((block for block in enabled if block.type == block_type), None)

        crt = first("trigger.crt")
        order = first("action.order")
        if crt is None or order is None:
            raise ValueError("Strategy Builder definitions require trigger.crt and action.order blocks.")
        bias = first("filter.bias")
        trend_blocks = tuple(block for block in enabled if block.type == "filter.trend")
        timeframes = sorted(
            {
                "M1",
                *(definition.timeframes or []),
                *(block.timeframe for block in enabled if block.timeframe),
                *(bias.params.get("timeframes", []) if bias else []),
            }
        )
        return BlueprintRuntimeConfig(
            crt_block=crt,
            swing_block=first("trigger.swing_sequence"),
            leg_block=first("trigger.leg"),
            target_block=first("compute.target"),
            fib_block=first("compute.fibonacci"),
            retracement_block=first("condition.retracement"),
            ir_block=first("trigger.immediate_rebalance"),
            structure_block=first("trigger.bos_mss"),
            session_block=first("detect.session_range"),
            amd_block=first("detect.amd_phase"),
            order_block=order,
            bias_block=bias,
            trend_blocks=trend_blocks,
            confluence_block=first("filter.confluence"),
            timeframes=tuple(timeframe for timeframe in timeframes if timeframe != "D1"),
        )

    def _params_from_definition(self, definition: StrategyDefinitionPayload, runtime: BlueprintRuntimeConfig) -> StrategyParams:
        payload: dict[str, Any] = dict(definition.global_params or {})
        payload["execution"] = ExecutionParams.model_validate(definition.execution or {}).model_dump()
        params = StrategyParams.model_validate(payload)
        params.execution = ExecutionParams.model_validate(payload["execution"])
        params.detect_c3 = bool(runtime.crt_block.params.get("detect_c3", params.detect_c3))
        params.crt_model = runtime.crt_block.params.get("model", params.crt_model)
        if runtime.fib_block:
            params.ote_deep = float(runtime.fib_block.params.get("deep", params.ote_deep))
        if runtime.retracement_block:
            zone_types = [item.upper() for item in runtime.retracement_block.params.get("zone_types", ["OB", "FVG"])]
            if zone_types == ["OB"]:
                params.pd_mode = "OB_SOLID"
            elif zone_types == ["FVG"]:
                params.pd_mode = "FVG"
            else:
                params.pd_mode = "OB_OR_FVG"
        order_params = runtime.order_block.params
        params.sl_buffer_ticks = int(order_params.get("buffer_ticks", params.sl_buffer_ticks))
        params.execution.fill_policy = order_params.get("fill_policy", params.execution.fill_policy)
        return params

    def _prepare_market_data(self, m1: pd.DataFrame) -> BlueprintMarketData:
        needed = {timeframe for timeframe in self.runtime.timeframes if timeframe != "M1"}
        frames = {"M1": m1, **build_timeframes(m1, tuple(sorted(needed)))}
        pivots = {
            timeframe: sorted(detect_pivots(frame, timeframe), key=lambda pivot: pivot.confirmation_time)
            for timeframe, frame in frames.items()
            if timeframe in {"H4", "H1", "M30", "M15", "M5", "M1"}
        }
        zone_timeframes = {self._retracement_timeframe()}
        fvgs = {timeframe: sorted(detect_fvgs(frames[timeframe]), key=lambda zone: zone.created_time) for timeframe in zone_timeframes}
        obs = {
            timeframe: sorted(
                detect_order_blocks(
                    frames[timeframe],
                    sensitivity_mode=self.params.ob_sensitivity_mode,
                    atr_len=self.params.ob_atr_len,
                    ob1_sensitivity=self.params.ob1_sensitivity,
                    ob2_sensitivity=self.params.ob2_sensitivity,
                    ob_min_body_ratio=self.params.ob_min_body_ratio,
                    ob_lookback1=self.params.ob_lookback1,
                    ob_lookback2_from=self.params.ob_lookback2_from,
                    ob_lookback2_to=self.params.ob_lookback2_to,
                ),
                key=lambda zone: zone.created_time,
            )
            for timeframe in zone_timeframes
        }
        ir_timeframes = {self._ir_timeframe()} if self.runtime.ir_block else set()
        irs = {
            timeframe: sorted(
                detect_immediate_rebalances(
                    frames[timeframe],
                    timeframe=timeframe,
                    tick_size=self.tick_size,
                    **self._ir_detection_params(),
                ),
                key=lambda ir: ir.available_time,
            )
            for timeframe in ir_timeframes
        }
        structure_timeframes = {self._structure_timeframe()} if self.runtime.structure_block else set()
        structure_breaks = {
            timeframe: sorted(
                detect_structure_breaks(frames[timeframe], pivots.get(timeframe, []), timeframe=timeframe),
                key=lambda item: item.available_time,
            )
            for timeframe in structure_timeframes
        }
        amd_timeframes = {self._amd_timeframe()} if self.runtime.amd_block else set()
        amd_phases = {
            timeframe: sorted(
                detect_amd_phases(
                    frames[timeframe],
                    timeframe=timeframe,
                    range_bars=int(self.runtime.amd_block.params.get("range_bars", 30)) if self.runtime.amd_block else 30,
                ),
                key=lambda item: item.available_time,
            )
            for timeframe in amd_timeframes
        }
        return BlueprintMarketData(
            m1=m1,
            frames=frames,
            pivots=pivots,
            fvgs=fvgs,
            obs=obs,
            irs=irs,
            structure_breaks=structure_breaks,
            amd_phases=amd_phases,
        )

    def _crt_events(self, frame: pd.DataFrame) -> list[BlueprintCrtEvent]:
        events: list[BlueprintCrtEvent] = []
        timeframe = self.runtime.crt_block.timeframe or "H1"
        delta = timeframe_delta(timeframe)
        if len(frame) < 2:
            return events
        for idx in range(1, len(frame)):
            c1 = frame.iloc[idx - 1].to_dict()
            c2 = frame.iloc[idx].to_dict()
            signal = crt_signal(c1, c2, detect_c3=self.params.detect_c3, model=self.params.crt_model)
            if signal is None:
                continue
            source_time = pd.Timestamp(frame.iloc[idx]["time_open"])
            events.append(
                BlueprintCrtEvent(
                    available_time=source_time + delta,
                    source_time=source_time,
                    timeframe=timeframe,
                    direction=signal.direction,
                    c1_high=signal.c1_high,
                    c1_low=signal.c1_low,
                    c1_mid=signal.c1_mid,
                    c2_close=signal.c2_close,
                    is_c3=signal.is_c3,
                )
            )
        return events

    def _advance_known_market_state(
        self,
        market: BlueprintMarketData,
        state: BlueprintState,
        current_time: pd.Timestamp,
        events: list[dict],
    ) -> tuple[dict[str, list[Pivot]], dict[str, list[ImmediateRebalance]]]:
        new_pivots: dict[str, list[Pivot]] = {}
        new_irs: dict[str, list[ImmediateRebalance]] = {}
        for timeframe, pivots in market.pivots.items():
            new_pivots[timeframe] = []
            while state.pivot_index[timeframe] < len(pivots):
                pivot = pivots[state.pivot_index[timeframe]]
                if pivot.confirmation_time > current_time:
                    break
                state.known_pivots[timeframe].append(pivot)
                new_pivots[timeframe].append(pivot)
                if timeframe in {"M15", "M1"}:
                    events.append(
                        self._event(
                            f"{timeframe}_PIVOT_CONFIRMED",
                            pivot.confirmation_time,
                            setup_id="market",
                            metadata={**pivot.__dict__, "timeframe": timeframe},
                        )
                    )
                state.pivot_index[timeframe] += 1

        for timeframe, zones in market.fvgs.items():
            while state.fvg_index[timeframe] < len(zones) and zones[state.fvg_index[timeframe]].created_time <= current_time:
                state.known_fvgs[timeframe].append(zones[state.fvg_index[timeframe]])
                state.fvg_index[timeframe] += 1
        for timeframe, zones in market.obs.items():
            while state.ob_index[timeframe] < len(zones) and zones[state.ob_index[timeframe]].created_time <= current_time:
                state.known_obs[timeframe].append(zones[state.ob_index[timeframe]])
                state.ob_index[timeframe] += 1
        for timeframe, irs in market.irs.items():
            new_irs[timeframe] = []
            while state.ir_index[timeframe] < len(irs) and irs[state.ir_index[timeframe]].available_time <= current_time:
                ir = irs[state.ir_index[timeframe]]
                state.known_irs[timeframe].append(ir)
                new_irs[timeframe].append(ir)
                events.append(
                    self._event(
                        "IMMEDIATE_REBALANCE_FOUND",
                        ir.available_time,
                        setup_id="market",
                        direction=ir.direction,
                        price=ir.rebalance_price,
                        metadata={
                            **ir.metadata,
                            "timeframe": timeframe,
                            "origin_time": ir.origin_time,
                            "impulse_time": ir.impulse_time,
                            "rebalance_time": ir.rebalance_time,
                            "origin_price": ir.origin_price,
                            "rebalance_price": ir.rebalance_price,
                            "invalidation_price": ir.invalidation_price,
                            "tolerance": ir.tolerance,
                        },
                    )
                )
                state.ir_index[timeframe] += 1
        for timeframe, breaks in market.structure_breaks.items():
            while (
                state.structure_break_index[timeframe] < len(breaks)
                and breaks[state.structure_break_index[timeframe]].available_time <= current_time
            ):
                structure_break = breaks[state.structure_break_index[timeframe]]
                state.known_structure_breaks[timeframe].append(structure_break)
                events.append(
                    self._event(
                        f"{structure_break.kind}_FOUND",
                        structure_break.available_time,
                        setup_id="market",
                        direction=structure_break.direction,
                        price=structure_break.level,
                        metadata={
                            **structure_break.metadata,
                            "timeframe": timeframe,
                            "kind": structure_break.kind,
                            "break_time": structure_break.break_time,
                            "pivot_time": structure_break.pivot_time,
                            "close": structure_break.close,
                            "previous_trend": structure_break.previous_trend,
                        },
                    )
                )
                state.structure_break_index[timeframe] += 1
        for timeframe, phases in market.amd_phases.items():
            while state.amd_phase_index[timeframe] < len(phases) and phases[state.amd_phase_index[timeframe]].available_time <= current_time:
                phase = phases[state.amd_phase_index[timeframe]]
                state.known_amd_phases[timeframe].append(phase)
                events.append(
                    self._event(
                        "AMD_PHASE_FOUND",
                        phase.available_time,
                        setup_id="market",
                        direction=phase.direction,
                        price=phase.range_high if phase.direction == "bearish" else phase.range_low,
                        metadata={
                            **phase.metadata,
                            "timeframe": timeframe,
                            "phase": phase.phase,
                            "range_start": phase.range_start,
                            "range_end": phase.range_end,
                            "range_high": phase.range_high,
                            "range_low": phase.range_low,
                            "sweep_time": phase.sweep_time,
                            "displacement_time": phase.displacement_time,
                        },
                    )
                )
                state.amd_phase_index[timeframe] += 1
        return new_pivots, new_irs

    def _build_setup_from_signal(
        self,
        setup_id: str,
        signal: BlueprintCrtEvent,
        state: BlueprintState,
        current_time: pd.Timestamp,
        events: list[dict],
    ) -> ActiveSetup | None:
        swing_tf = self._swing_timeframe()
        pivots = state.known_pivots.get(swing_tf, [])
        wanted_kind = "low" if signal.direction == "bullish" else "high"
        wanted = [pivot for pivot in pivots if pivot.kind == wanted_kind and pivot.confirmation_time <= current_time]
        if len(wanted) < 2:
            events.append(
                self._event(
                    "SETUP_REJECTED_NO_DOUBLE_SWING",
                    current_time,
                    setup_id=setup_id,
                    direction=signal.direction,
                    state_before="WAITING_SWINGS",
                    state_after="INVALIDATED",
                    metadata={"block_id": self.runtime.swing_block.id if self.runtime.swing_block else None, "timeframe": swing_tf},
                )
            )
            return None
        s1, s2 = wanted[-2], wanted[-1]
        pattern = self._swing_pattern(signal.direction, s1, s2)
        allowed = self._allowed_swing_patterns()
        if pattern not in allowed:
            events.append(
                self._event(
                    "BLUEPRINT_BLOCK_REJECTED",
                    current_time,
                    setup_id=setup_id,
                    direction=signal.direction,
                    state_before="WAITING_SWINGS",
                    state_after="INVALIDATED",
                    metadata={"block_id": self.runtime.swing_block.id if self.runtime.swing_block else None, "pattern": pattern},
                )
            )
            return None
        next_state = "WAITING_IR" if self.runtime.ir_block and self.runtime.leg_block is None else "WAITING_LEG"
        setup = ActiveSetup(
            setup_id=setup_id,
            direction=signal.direction,
            s1=s1,
            s2=s2,
            c1_high=signal.c1_high,
            c1_low=signal.c1_low,
            tp=signal.c1_high if signal.direction == "bullish" else signal.c1_low,
            state=next_state,
            metadata={
                "strategy_builder": True,
                "crt_block": self.runtime.crt_block.id,
                "swing_block": self.runtime.swing_block.id if self.runtime.swing_block else None,
                "ir_block": self.runtime.ir_block.id if self.runtime.ir_block else None,
                "crt_timeframe": signal.timeframe,
                "swing_timeframe": swing_tf,
                "swing_pattern": pattern,
            },
        )
        events.append(
            self._event(
                "M15_DOUBLE_SWING_VALIDATED" if swing_tf == "M15" else "SWING_SEQUENCE_VALIDATED",
                current_time,
                setup_id=setup_id,
                direction=signal.direction,
                state_before="WAITING_SWINGS",
                state_after=next_state,
                metadata={
                    **setup.metadata,
                    "s1_time": s1.pivot_time,
                    "s1_price": s1.price,
                    "s2_time": s2.pivot_time,
                    "s2_price": s2.price,
                },
            )
        )
        return setup

    def _advance_active_blueprint_setup(
        self,
        active: ActiveSetup,
        new_pivots: dict[str, list[Pivot]],
        new_irs: dict[str, list[ImmediateRebalance]],
        state: BlueprintState,
        row: pd.Series,
        current_time: pd.Timestamp,
        events: list[dict],
    ) -> ActiveSetup | None:
        if active.state == "WAITING_IR":
            selected_ir = self._select_new_ir(active, new_irs)
            if selected_ir is not None:
                tolerance = max(selected_ir.tolerance, self.tick_size)
                bottom = selected_ir.origin_price - tolerance
                top = selected_ir.origin_price + tolerance
                zone = PriceZone(
                    kind="IR",
                    direction=active.direction,
                    bottom=min(bottom, top),
                    top=max(bottom, top),
                    created_time=selected_ir.available_time,
                    source_time=selected_ir.origin_time,
                    metadata={
                        **selected_ir.metadata,
                        "timeframe": selected_ir.timeframe,
                        "origin_time": selected_ir.origin_time,
                        "impulse_time": selected_ir.impulse_time,
                        "rebalance_time": selected_ir.rebalance_time,
                        "origin_price": selected_ir.origin_price,
                        "rebalance_price": selected_ir.rebalance_price,
                        "invalidation_price": selected_ir.invalidation_price,
                    },
                )
                active.pd_zone = zone
                active.pd_mitigated = True
                active.state = "ORDER_PENDING"
                active.metadata.update(
                    {
                        "ir_block": self.runtime.ir_block.id if self.runtime.ir_block else None,
                        "ir_timeframe": selected_ir.timeframe,
                        "pd_type": "IR",
                        "pd_top": zone.top,
                        "pd_bottom": zone.bottom,
                        "pd_mid": zone.midpoint,
                        "ir_origin_time": selected_ir.origin_time,
                        "ir_impulse_time": selected_ir.impulse_time,
                        "ir_rebalance_time": selected_ir.rebalance_time,
                        "ir_origin_price": selected_ir.origin_price,
                        "ir_rebalance_price": selected_ir.rebalance_price,
                        "ir_invalidation_price": selected_ir.invalidation_price,
                        "ir_impulse_body_low": selected_ir.impulse_body_low,
                        "ir_impulse_body_high": selected_ir.impulse_body_high,
                    }
                )
                events.append(
                    self._event(
                        "IMMEDIATE_REBALANCE_SELECTED",
                        selected_ir.available_time,
                        setup_id=active.setup_id,
                        direction=active.direction,
                        price=selected_ir.rebalance_price,
                        state_before="WAITING_IR",
                        state_after="ORDER_PENDING",
                        metadata=active.metadata,
                    )
                )
                return active

        leg_tf = self._leg_timeframe()
        if active.state == "WAITING_LEG":
            wanted_kind = "high" if active.direction == "bullish" else "low"
            for pivot in new_pivots.get(leg_tf, []):
                if pivot.kind == wanted_kind and pivot.pivot_time > active.s2.pivot_time:
                    active.leg_end = pivot
                    active.ote_bottom, active.ote_top = ote_zone(active.s2.price, pivot.price, self.params.ote_deep)
                    active.state = "WAITING_PD_ARRAY"
                    active.metadata.update({"leg_timeframe": leg_tf, "leg_block": self.runtime.leg_block.id if self.runtime.leg_block else None})
                    events.append(
                        self._event(
                            "LEG_FOUND",
                            pivot.confirmation_time,
                            setup_id=active.setup_id,
                            direction=active.direction,
                            price=pivot.price,
                            state_before="WAITING_LEG",
                            state_after="WAITING_PD_ARRAY",
                            metadata={**active.metadata, "leg_end_time": pivot.pivot_time},
                        )
                    )
                    events.append(
                        self._event(
                            "OTE_CREATED",
                            pivot.confirmation_time,
                            setup_id=active.setup_id,
                            direction=active.direction,
                            state_before="WAITING_PD_ARRAY",
                            state_after="WAITING_PD_ARRAY",
                            metadata={**active.metadata, "ote_bottom": active.ote_bottom, "ote_top": active.ote_top},
                        )
                    )
                    break

        if active.state == "WAITING_PD_ARRAY":
            assert active.leg_end is not None
            assert active.ote_bottom is not None
            assert active.ote_top is not None
            retracement_tf = self._retracement_timeframe()
            selected = select_pd_array(
                state.known_obs.get(retracement_tf, []),
                state.known_fvgs.get(retracement_tf, []),
                active.direction,
                active.s2.pivot_time,
                active.leg_end.pivot_time,
                active.ote_bottom,
                active.ote_top,
                pd_mode=self.params.pd_mode,
                require_midpoint=self._retracement_requires_midpoint(),
            )
            if selected is not None:
                if self.runtime.confluence_block and not self._zone_has_confluence(selected, state):
                    events.append(
                        self._event(
                            "BLUEPRINT_BLOCK_REJECTED",
                            current_time,
                            setup_id=active.setup_id,
                            direction=active.direction,
                            price=selected.midpoint,
                            state_before="WAITING_PD_ARRAY",
                            state_after="WAITING_PD_ARRAY",
                            metadata={"block_id": self.runtime.confluence_block.id, "reason": "no_htf_overlap"},
                        )
                    )
                    return active
                active.pd_zone = selected
                active.state = "WAITING_MITIGATION"
                active.metadata.update(
                    {
                        "retracement_block": self.runtime.retracement_block.id if self.runtime.retracement_block else None,
                        "pd_timeframe": retracement_tf,
                        "pd_type": selected.kind,
                        "pd_top": selected.top,
                        "pd_bottom": selected.bottom,
                        "pd_mid": selected.midpoint,
                    }
                )
                events.append(
                    self._event(
                        "OB_SELECTED" if selected.kind == "OB" else "FVG_SELECTED",
                        current_time,
                        setup_id=active.setup_id,
                        direction=active.direction,
                        price=selected.midpoint,
                        state_before="WAITING_PD_ARRAY",
                        state_after="WAITING_MITIGATION",
                        metadata=active.metadata,
                    )
                )

        if active.state in {"WAITING_MITIGATION", "WAITING_REJECTION"} and active.pd_zone is not None:
            zone = active.pd_zone
            if not active.pd_mitigated and pd_touched(row, zone.bottom, zone.top):
                active.pd_mitigated = True
                active.state = "WAITING_REJECTION"
                events.append(
                    self._event(
                        "PD_TOUCHED",
                        current_time,
                        setup_id=active.setup_id,
                        direction=active.direction,
                        price=zone.midpoint,
                        state_before="WAITING_MITIGATION",
                        state_after="WAITING_REJECTION",
                        metadata=active.metadata,
                    )
                )
                if self._retracement_confirmation() == "touch":
                    active.state = "ORDER_PENDING"
                    return active
            if rejection_confirmed(active.direction, row, zone.midpoint, active.pd_mitigated):
                events.append(
                    self._event(
                        "REJECTION_CONFIRMED",
                        current_time,
                        setup_id=active.setup_id,
                        direction=active.direction,
                        price=float(row["close"]),
                        state_before="WAITING_REJECTION",
                        state_after="ORDER_PENDING",
                        metadata=active.metadata,
                    )
                )
                active.state = "ORDER_PENDING"
        return active

    def _pending_from_blueprint_setup(
        self,
        active: ActiveSetup,
        current_time: pd.Timestamp,
        row: pd.Series,
        state: BlueprintState,
        m1: pd.DataFrame,
        events: list[dict],
        order_rows: list[dict],
    ) -> PendingEntry | None:
        assert active.pd_zone is not None
        entry_price = float(row["close"])
        sl = self._stop_loss(active)
        initial_tp = active.tp
        tp, target_metadata = self._target_price(active, entry_price, initial_tp, current_time, state, m1)
        if not risk_is_valid(active.direction, entry_price, sl, tp):
            events.append(self._risk_rejected_event(active, current_time, entry_price, sl, tp, "invalid_directional_risk", target_metadata))
            return None
        risk = abs(entry_price - sl)
        reward = abs(tp - entry_price)
        planned_rr = reward / risk if risk else 0.0
        min_rr = float(self.runtime.order_block.params.get("min_rr", 0.0))
        if planned_rr < min_rr:
            events.append(
                self._risk_rejected_event(
                    active,
                    current_time,
                    entry_price,
                    sl,
                    tp,
                    "below_min_rr",
                    {**target_metadata, "planned_rr": planned_rr, "min_rr": min_rr},
                )
            )
            return None
        order_ref = f"{active.setup_id}-entry"
        metadata = {
            **active.metadata,
            **target_metadata,
            "order_block": self.runtime.order_block.id,
            "sl_model": self.runtime.order_block.params.get("stop_loss", "structural_pd_array"),
            "tp_model": self.runtime.order_block.params.get("take_profit", "crt_objective"),
            "planned_rr": planned_rr,
            "entry_price": entry_price,
            "sl": sl,
            "tp": tp,
        }
        pending = PendingEntry(
            order_ref=order_ref,
            setup=active,
            direction=active.direction,
            requested_time=current_time,
            volume=self.params.execution.order_qty,
            sl=sl,
            tp=tp,
            pd_type=active.pd_zone.kind,
            metadata=metadata,
        )
        self._ensure_order_row(pending, entry_price, order_rows)
        events.append(
            self._event(
                "ORDER_CREATED",
                current_time,
                setup_id=active.setup_id,
                direction=active.direction,
                price=entry_price,
                state_before="ORDER_PENDING",
                state_after="ORDER_PENDING",
                metadata=metadata,
            )
        )
        return pending

    def _risk_rejected_event(
        self,
        active: ActiveSetup,
        current_time: pd.Timestamp,
        entry_price: float,
        sl: float,
        tp: float,
        reason: str,
        extra: dict[str, Any] | None = None,
    ) -> dict:
        return self._event(
            "RISK_REJECTED",
            current_time,
            setup_id=active.setup_id,
            direction=active.direction,
            price=entry_price,
            state_before="ORDER_PENDING",
            state_after="INVALIDATED",
            metadata={**active.metadata, "sl": sl, "tp": tp, "reason": reason, **(extra or {})},
        )

    def _stop_loss(self, active: ActiveSetup) -> float:
        assert active.pd_zone is not None
        buffer_ticks = int(self.runtime.order_block.params.get("buffer_ticks", self.params.sl_buffer_ticks))
        stop_model = str(self.runtime.order_block.params.get("stop_loss", "structural_pd_array"))
        if stop_model == "immediate_rebalance_origin" and active.metadata.get("ir_origin_price") is not None:
            origin = float(active.metadata["ir_origin_price"])
            if active.direction == "bullish":
                return origin - buffer_ticks * self.tick_size
            return origin + buffer_ticks * self.tick_size
        if active.direction == "bullish":
            candidates = [active.pd_zone.bottom]
            if active.ote_bottom is not None:
                candidates.append(active.ote_bottom)
            return min(candidates) - buffer_ticks * self.tick_size
        candidates = [active.pd_zone.top]
        if active.ote_top is not None:
            candidates.append(active.ote_top)
        return max(candidates) + buffer_ticks * self.tick_size

    def _target_price(
        self,
        active: ActiveSetup,
        entry_price: float,
        initial_tp: float,
        current_time: pd.Timestamp,
        state: BlueprintState,
        m1: pd.DataFrame,
    ) -> tuple[float, dict[str, Any]]:
        mode = self.runtime.order_block.params.get("take_profit", "crt_objective")
        if mode not in {"crt_or_nearest_liquidity", "nearest_liquidity"}:
            return initial_tp, {"target_model": "crt_objective", "target_price": initial_tp}
        candidate, candidates = self._nearest_liquidity(active.direction, entry_price, initial_tp, current_time, state, m1)
        if candidate is None:
            return initial_tp, {"target_model": "crt_objective", "target_price": initial_tp, "target_candidates": candidates}
        initial_reward = abs(initial_tp - entry_price)
        candidate_reward = abs(candidate["price"] - entry_price)
        max_multiplier = float(self.runtime.order_block.params.get("max_rr_multiplier_from_initial", 2.0))
        if initial_reward > 0 and candidate_reward <= initial_reward * max_multiplier:
            return float(candidate["price"]), {
                "target_model": "nearest_liquidity",
                "target_price": float(candidate["price"]),
                "target_source": candidate["source"],
                "target_candidates": candidates,
                "initial_target": initial_tp,
            }
        return initial_tp, {
            "target_model": "crt_objective",
            "target_price": initial_tp,
            "rejected_liquidity_target": candidate,
            "target_candidates": candidates,
        }

    def _nearest_liquidity(
        self,
        direction: str,
        entry_price: float,
        initial_tp: float,
        current_time: pd.Timestamp,
        state: BlueprintState,
        m1: pd.DataFrame,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        candidates: list[dict[str, Any]] = []
        levels = self._target_levels()
        if "previous_day_high_low" in levels:
            previous_day = self._previous_period_level(direction, current_time, m1, "day")
            if previous_day is not None:
                candidates.append(previous_day)
        if "previous_week_high_low" in levels:
            previous_week = self._previous_period_level(direction, current_time, m1, "week")
            if previous_week is not None:
                candidates.append(previous_week)
        if "previous_month_high_low" in levels:
            previous_month = self._previous_period_level(direction, current_time, m1, "month")
            if previous_month is not None:
                candidates.append(previous_month)
        session_timezone = str((self.definition.global_params or {}).get("timezone", "America/New_York"))
        for level_name, session_name in (
            ("asian_high_low", "asian"),
            ("london_high_low", "london"),
            ("new_york_high_low", "new_york"),
        ):
            if level_name not in levels:
                continue
            session = latest_completed_session_range(m1, current_time, session_name, timezone_name=session_timezone)
            if session is None:
                continue
            if direction == "bullish":
                candidates.append(
                    {
                        "price": session.high,
                        "source": f"{session_name}_high",
                        "session_start": session.start_time,
                        "session_end": session.end_time,
                    }
                )
            else:
                candidates.append(
                    {
                        "price": session.low,
                        "source": f"{session_name}_low",
                        "session_start": session.start_time,
                        "session_end": session.end_time,
                    }
                )
        if "equal_highs_lows" in levels:
            wanted_kind = "high" if direction == "bullish" else "low"
            for timeframe in ("H1", "M15", "M1"):
                for level in detect_equal_high_lows(state.known_pivots.get(timeframe, []), tick_size=self.tick_size)[-12:]:
                    if level.kind == wanted_kind and level.available_time <= current_time:
                        candidates.append(
                            {
                                "price": level.price,
                                "source": f"{timeframe}_equal_{wanted_kind}s",
                                "available_time": level.available_time,
                                "touches": level.touches,
                                "tolerance": level.tolerance,
                            }
                        )
        if "h1_m15_swings" in levels:
            wanted_kind = "high" if direction == "bullish" else "low"
            for timeframe in ("H1", "M15"):
                for pivot in state.known_pivots.get(timeframe, [])[-8:]:
                    if pivot.kind == wanted_kind and pivot.confirmation_time <= current_time:
                        candidates.append(
                            {
                                "price": pivot.price,
                                "source": f"{timeframe}_swing_{wanted_kind}",
                                "available_time": pivot.confirmation_time,
                                "pivot_time": pivot.pivot_time,
                            }
                        )
        candidates = sorted(candidates, key=lambda item: abs(float(item["price"]) - entry_price))
        if direction == "bullish":
            usable = [item for item in candidates if item["price"] > max(entry_price, initial_tp)]
            return min(usable, key=lambda item: item["price"] - entry_price, default=None), candidates
        usable = [item for item in candidates if item["price"] < min(entry_price, initial_tp)]
        return min(usable, key=lambda item: entry_price - item["price"], default=None), candidates

    def _target_levels(self) -> set[str]:
        if self.runtime.target_block is None:
            return {"previous_day_high_low", "previous_week_high_low", "previous_month_high_low", "h1_m15_swings"}
        return set(self.runtime.target_block.params.get("levels", ["previous_day_high_low", "h1_m15_swings"]))

    def _previous_period_level(
        self,
        direction: str,
        current_time: pd.Timestamp,
        m1: pd.DataFrame,
        period: str,
    ) -> dict[str, Any] | None:
        times = pd.to_datetime(m1["time_open"], utc=True)
        current_utc = current_time.tz_convert("UTC") if current_time.tzinfo else current_time.tz_localize("UTC")
        if period == "day":
            start = current_utc.normalize()
            end = start
            start = start - pd.Timedelta(days=1)
        elif period == "week":
            end = current_utc.normalize() - pd.Timedelta(days=int(current_utc.weekday()))
            start = end - pd.Timedelta(days=7)
        elif period == "month":
            end = pd.Timestamp(year=current_utc.year, month=current_utc.month, day=1, tz="UTC")
            if end.month == 1:
                start = pd.Timestamp(year=end.year - 1, month=12, day=1, tz="UTC")
            else:
                start = pd.Timestamp(year=end.year, month=end.month - 1, day=1, tz="UTC")
        else:
            raise ValueError(f"Unsupported target period: {period}")
        frame = m1[(times >= start) & (times < end)]
        if frame.empty:
            return None
        if direction == "bullish":
            return {"price": float(frame["high"].max()), "source": f"previous_{period}_high"}
        return {"price": float(frame["low"].min()), "source": f"previous_{period}_low"}

    def _bias_allows(self, direction: str, state: BlueprintState, current_time: pd.Timestamp) -> bool:
        assert self.runtime.bias_block is not None
        timeframes = [item.upper() for item in self.runtime.bias_block.params.get("timeframes", ["H1", "M15"])]
        neutral_policy = self.runtime.bias_block.params.get("neutral_policy", "reject")
        for timeframe in timeframes:
            bias = self._swing_bias(state.known_pivots.get(timeframe, []), current_time)
            if bias == "neutral":
                if neutral_policy == "allow":
                    continue
                return False
            if bias != direction:
                return False
        return True

    def _trend_allows(
        self,
        signal_direction: str,
        state: BlueprintState,
        current_time: pd.Timestamp,
    ) -> tuple[bool, dict[str, Any]]:
        if not self.runtime.trend_blocks:
            return True, {}
        parent_direction = signal_direction
        snapshots: list[dict[str, Any]] = []
        for block in self.runtime.trend_blocks:
            timeframe = (block.timeframe or "H1").upper()
            params = block.params or {}
            bias = self._trend_bias_for_block(block, state, current_time)
            wanted = str(params.get("direction", "follow_signal"))
            if wanted == "follow_signal":
                expected = signal_direction
            elif wanted == "follow_parent":
                expected = parent_direction
            elif wanted in {"bullish", "bearish"}:
                expected = wanted
            else:
                expected = signal_direction
            neutral_policy = str(params.get("neutral_policy", "reject"))
            snapshot = {
                "block_id": block.id,
                "block_type": block.type,
                "timeframe": timeframe,
                "trend": bias,
                "expected": expected,
                "lookback": params.get("lookback", "all_known"),
            }
            snapshots.append(snapshot)
            if bias == "neutral":
                if neutral_policy == "allow":
                    continue
                return False, {"reason": "neutral_trend", "trends": snapshots, **snapshot}
            if bias != expected:
                return False, {"reason": "trend_mismatch", "trends": snapshots, **snapshot}
            parent_direction = bias
        return True, {"trends": snapshots}

    def _trend_bias_for_block(self, block: StrategyBlock, state: BlueprintState, current_time: pd.Timestamp) -> str:
        timeframe = (block.timeframe or "H1").upper()
        lookback = str((block.params or {}).get("lookback", "all_known"))
        pivots = state.known_pivots.get(timeframe, [])
        cutoff = self._trend_cutoff(current_time, lookback)
        if cutoff is not None:
            pivots = [pivot for pivot in pivots if pivot.confirmation_time >= cutoff]
        return self._swing_bias(pivots, current_time)

    def _trend_cutoff(self, current_time: pd.Timestamp, lookback: str) -> pd.Timestamp | None:
        if lookback == "rolling_day":
            return current_time - pd.Timedelta(days=1)
        if lookback == "rolling_week":
            return current_time - pd.Timedelta(days=7)
        if lookback == "rolling_month":
            return current_time - pd.Timedelta(days=31)
        return None

    def _structure_allows(
        self,
        signal_direction: str,
        state: BlueprintState,
        current_time: pd.Timestamp,
    ) -> tuple[bool, dict[str, Any]]:
        block = self.runtime.structure_block
        if block is None:
            return True, {}
        timeframe = self._structure_timeframe()
        params = block.params or {}
        wanted_direction = str(params.get("direction", "follow_signal"))
        expected_direction = signal_direction if wanted_direction == "follow_signal" else wanted_direction
        mode = str(params.get("mode", "BOS_OR_MSS")).upper()
        allowed_kinds = {"BOS", "MSS"} if mode == "BOS_OR_MSS" else {mode}
        lookback_bars = int(params.get("lookback_bars", 48))
        candidates = [
            item
            for item in state.known_structure_breaks.get(timeframe, [])[-lookback_bars:]
            if item.available_time <= current_time
            and item.direction == expected_direction
            and item.kind in allowed_kinds
        ]
        metadata = {
            "block_id": block.id,
            "block_type": block.type,
            "timeframe": timeframe,
            "expected_direction": expected_direction,
            "mode": mode,
            "lookback_bars": lookback_bars,
        }
        if not candidates:
            return False, {**metadata, "reason": "no_confirmed_structure_break"}
        latest = candidates[-1]
        return True, {
            **metadata,
            "structure_break": {
                "kind": latest.kind,
                "direction": latest.direction,
                "available_time": latest.available_time,
                "break_time": latest.break_time,
                "level": latest.level,
                "close": latest.close,
                "previous_trend": latest.previous_trend,
            },
        }

    def _amd_allows(
        self,
        signal_direction: str,
        state: BlueprintState,
        current_time: pd.Timestamp,
    ) -> tuple[bool, dict[str, Any]]:
        block = self.runtime.amd_block
        if block is None:
            return True, {}
        timeframe = self._amd_timeframe()
        params = block.params or {}
        allowed_phases = {str(item) for item in params.get("phases", ["accumulation_candidate", "distribution_candidate"])}
        lookback_bars = int(params.get("lookback_bars", 96))
        candidates = [
            item
            for item in state.known_amd_phases.get(timeframe, [])[-lookback_bars:]
            if item.available_time <= current_time
            and item.direction == signal_direction
            and item.phase in allowed_phases
        ]
        metadata = {
            "block_id": block.id,
            "block_type": block.type,
            "timeframe": timeframe,
            "allowed_phases": sorted(allowed_phases),
            "lookback_bars": lookback_bars,
        }
        if not candidates:
            return False, {**metadata, "reason": "no_confirmed_amd_phase"}
        latest = candidates[-1]
        return True, {
            **metadata,
            "amd_phase": {
                "phase": latest.phase,
                "direction": latest.direction,
                "available_time": latest.available_time,
                "range_high": latest.range_high,
                "range_low": latest.range_low,
                "sweep_time": latest.sweep_time,
                "displacement_time": latest.displacement_time,
            },
        }

    def _swing_bias(self, pivots: list[Pivot], current_time: pd.Timestamp) -> str:
        known = [pivot for pivot in pivots if pivot.confirmation_time <= current_time]
        highs = [pivot for pivot in known if pivot.kind == "high"][-2:]
        lows = [pivot for pivot in known if pivot.kind == "low"][-2:]
        if len(highs) < 2 or len(lows) < 2:
            return "neutral"
        if highs[-1].price > highs[-2].price and lows[-1].price > lows[-2].price:
            return "bullish"
        if highs[-1].price < highs[-2].price and lows[-1].price < lows[-2].price:
            return "bearish"
        return "neutral"

    def _zone_has_confluence(self, selected: PriceZone, state: BlueprintState) -> bool:
        if self.runtime.confluence_block is None:
            return True
        timeframes = [item.upper() for item in self.runtime.confluence_block.params.get("timeframes", ["H1", "M15"])]
        tolerance = float(self.runtime.confluence_block.params.get("overlap_tolerance_ticks", 4)) * self.tick_size
        overlaps = 0
        for timeframe in timeframes:
            zones = [*state.known_obs.get(timeframe, []), *state.known_fvgs.get(timeframe, [])]
            if any(zone.direction == selected.direction and zone.top + tolerance >= selected.bottom and zone.bottom - tolerance <= selected.top for zone in zones):
                overlaps += 1
        return overlaps >= min(2, len(timeframes))

    def _swing_pattern(self, direction: str, s1: Pivot, s2: Pivot) -> str:
        if direction == "bullish" and s2.price > s1.price:
            return "swing_inner"
        if direction == "bearish" and s2.price < s1.price:
            return "swing_inner"
        return "swing_swing"

    def _allowed_swing_patterns(self) -> set[str]:
        if self.runtime.swing_block is None:
            return {"swing_swing", "swing_inner"}
        params = self.runtime.swing_block.params
        values = params.get("patterns")
        if not values and "any" in params:
            values = [item.get("pattern") for item in params["any"] if isinstance(item, dict)]
        return {str(item) for item in (values or ["swing_swing"])}

    def _swing_timeframe(self) -> str:
        return (self.runtime.swing_block.timeframe if self.runtime.swing_block else "M15") or "M15"

    def _leg_timeframe(self) -> str:
        return (self.runtime.leg_block.timeframe if self.runtime.leg_block else self._swing_timeframe()) or "M15"

    def _retracement_timeframe(self) -> str:
        return (self.runtime.retracement_block.timeframe if self.runtime.retracement_block else "M1") or "M1"

    def _ir_timeframe(self) -> str:
        return (self.runtime.ir_block.timeframe if self.runtime.ir_block else "M1") or "M1"

    def _structure_timeframe(self) -> str:
        return (self.runtime.structure_block.timeframe if self.runtime.structure_block else "M1") or "M1"

    def _amd_timeframe(self) -> str:
        return (self.runtime.amd_block.timeframe if self.runtime.amd_block else "M1") or "M1"

    def _ir_detection_params(self) -> dict[str, Any]:
        if self.runtime.ir_block is None:
            return {}
        params = self.runtime.ir_block.params
        return {
            "tolerance_ticks": int(params.get("tolerance_ticks", 1)),
            "min_impulse_body_ratio": float(params.get("min_impulse_body_ratio", 0.55)),
            "min_impulse_body_ticks": int(params.get("min_impulse_body_ticks", 4)),
            "require_rejection_close": bool(params.get("require_rejection_close", True)),
        }

    def _select_new_ir(
        self,
        active: ActiveSetup,
        new_irs: dict[str, list[ImmediateRebalance]],
    ) -> ImmediateRebalance | None:
        if self.runtime.ir_block is None:
            return None
        for ir in new_irs.get(self._ir_timeframe(), []):
            if ir.direction != active.direction:
                continue
            if ir.rebalance_time <= active.s2.pivot_time:
                continue
            return ir
        return None

    def _retracement_requires_midpoint(self) -> bool:
        if self.runtime.retracement_block is None:
            return True
        zone = str(self.runtime.retracement_block.params.get("zone", "OTE")).upper()
        return zone in {"OTE", "0.5", "0.50"}

    def _retracement_confirmation(self) -> str:
        if self.runtime.retracement_block is None:
            return "rejection"
        return str(self.runtime.retracement_block.params.get("confirmation", "rejection"))
