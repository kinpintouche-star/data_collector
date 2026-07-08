from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import pandas as pd

from ict.backtest.broker_sim import SimulatedTrade, close_reason_for_bar, pnl_points
from ict.backtest.metrics import summarize_trades
from ict.data.candles import normalize_candles, timeframe_delta
from ict.data.gaps import CandleGapPlan, split_continuous_candles
from ict.strategy.ict_crt_m1 import prepare_market_data
from ict.strategy.indicators import (
    Pivot,
    crt_signal,
    ote_zone,
    pd_touched,
    rejection_confirmed,
    risk_is_valid,
    s2_invalidated,
)
from ict.strategy.params import StrategyParams
from ict.strategy.pd_arrays import PriceZone, detect_fvgs, detect_order_blocks, select_pd_array


@dataclass
class BacktestResult:
    events: list[dict] = field(default_factory=list)
    orders: pd.DataFrame = field(default_factory=pd.DataFrame)
    fills: pd.DataFrame = field(default_factory=pd.DataFrame)
    trades: pd.DataFrame = field(default_factory=pd.DataFrame)
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    metrics: dict = field(default_factory=dict)


@dataclass
class H1SignalEvent:
    available_time: pd.Timestamp
    c2_time: pd.Timestamp
    direction: Literal["bullish", "bearish"]
    c1_high: float
    c1_low: float
    c1_mid: float
    c2_close: float
    is_c3: bool


@dataclass
class ActiveSetup:
    setup_id: str
    direction: Literal["bullish", "bearish"]
    s1: Pivot
    s2: Pivot
    c1_high: float
    c1_low: float
    tp: float
    state: str = "WAITING_LEG"
    leg_end: Pivot | None = None
    ote_bottom: float | None = None
    ote_top: float | None = None
    pd_zone: PriceZone | None = None
    pd_mitigated: bool = False
    metadata: dict = field(default_factory=dict)


@dataclass
class OpenTrade:
    order_ref: str
    setup_id: str
    direction: Literal["bullish", "bearish"]
    entry_time: pd.Timestamp
    entry_index: int
    entry_price: float
    volume: float
    sl: float
    tp: float
    pd_type: str
    strategy_mode: str
    rr: float
    metadata: dict = field(default_factory=dict)


@dataclass
class PendingEntry:
    order_ref: str
    setup: ActiveSetup
    direction: Literal["bullish", "bearish"]
    requested_time: pd.Timestamp
    volume: float
    sl: float
    tp: float
    pd_type: str
    metadata: dict = field(default_factory=dict)


class BacktestEngine:
    """Event-driven offline reproduction of the Pine v1.6 setup chain."""

    gap_min_segment_rows = 120

    def __init__(self, params: StrategyParams, tick_size: float = 0.25):
        self.params = params
        self.tick_size = tick_size

    def run(self, m1_candles: pd.DataFrame) -> BacktestResult:
        m1 = normalize_candles(m1_candles)
        if m1.empty:
            trades = pd.DataFrame()
            return BacktestResult(trades=trades, metrics=summarize_trades(trades))

        gap_plan = split_continuous_candles(m1, "M1", min_segment_rows=self.gap_min_segment_rows)
        if gap_plan.gaps:
            return self._run_gap_segments(gap_plan)

        prepared = prepare_market_data(m1)
        h1_signals = self._h1_signal_events(prepared.h1)
        pivot_events = sorted(prepared.m15_pivots, key=lambda pivot: pivot.confirmation_time)
        fvg_events = sorted(detect_fvgs(m1), key=lambda zone: zone.created_time)
        ob_events = sorted(
            detect_order_blocks(
                m1,
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

        events: list[dict] = []
        order_rows: list[dict] = []
        fill_rows: list[dict] = []
        trade_rows: list[dict] = []
        equity_rows: list[dict] = []
        known_fvgs: list[PriceZone] = []
        known_obs: list[PriceZone] = []
        latest_high: Pivot | None = None
        previous_high: Pivot | None = None
        latest_low: Pivot | None = None
        previous_low: Pivot | None = None
        active: ActiveSetup | None = None
        open_trade: OpenTrade | None = None
        pending_entry: PendingEntry | None = None
        balance = float(self.params.execution.initial_balance)
        peak_equity = balance
        setup_counter = 0
        pivot_idx = 0
        h1_idx = 0
        fvg_idx = 0
        ob_idx = 0

        for bar_index, row in m1.iterrows():
            current_time = pd.Timestamp(row["time_open"])

            while fvg_idx < len(fvg_events) and fvg_events[fvg_idx].created_time <= current_time:
                known_fvgs.append(fvg_events[fvg_idx])
                fvg_idx += 1
            while ob_idx < len(ob_events) and ob_events[ob_idx].created_time <= current_time:
                known_obs.append(ob_events[ob_idx])
                ob_idx += 1

            new_pivots: list[Pivot] = []
            while pivot_idx < len(pivot_events) and pivot_events[pivot_idx].confirmation_time <= current_time:
                pivot = pivot_events[pivot_idx]
                new_pivots.append(pivot)
                if pivot.kind == "high":
                    previous_high = latest_high
                    latest_high = pivot
                else:
                    previous_low = latest_low
                    latest_low = pivot
                events.append(
                    self._event(
                        "M15_PIVOT_CONFIRMED",
                        pivot.confirmation_time,
                        setup_id="market",
                        metadata=pivot.__dict__,
                    )
                )
                pivot_idx += 1

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
                            metadata={"fill_policy": "next_open"},
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
                    event_type = "TRADE_CLOSED_TP" if close_reason == "TP" else "TRADE_CLOSED_SL"
                    events.append(
                        self._event(
                            event_type,
                            current_time,
                            setup_id=open_trade.setup_id,
                            direction=open_trade.direction,
                            price=exit_price,
                            state_before="IN_POSITION",
                            state_after="COMPLETED",
                            metadata={"pnl": trade_row["pnl"], "rr": trade_row["rr"]},
                        )
                    )
                    active = None
                    open_trade = None

            while h1_idx < len(h1_signals) and h1_signals[h1_idx].available_time <= current_time:
                signal = h1_signals[h1_idx]
                h1_idx += 1
                if open_trade is not None or pending_entry is not None:
                    continue
                if not self._trade_direction_allowed(signal.direction):
                    continue
                if self.params.only_killzone and not self._in_killzone(signal.c2_time):
                    continue

                setup_counter += 1
                setup_id = f"setup-{setup_counter}"
                events.append(
                    self._event(
                        "H1_SIGNAL",
                        current_time,
                        setup_id=setup_id,
                        direction=signal.direction,
                        price=signal.c2_close,
                        state_before="IDLE",
                        state_after="WAITING_LEG",
                        metadata=signal.__dict__,
                    )
                )
                setup = self._build_setup_from_signal(
                    setup_id,
                    signal,
                    previous_high,
                    latest_high,
                    previous_low,
                    latest_low,
                )
                if setup is None:
                    events.append(
                        self._event(
                            "SETUP_REJECTED_NO_DOUBLE_SWING",
                            current_time,
                            setup_id=setup_id,
                            direction=signal.direction,
                            state_before="WAITING_LEG",
                            state_after="INVALIDATED",
                        )
                    )
                else:
                    events.append(
                        self._event(
                            "M15_DOUBLE_SWING_VALIDATED",
                            current_time,
                            setup_id=setup_id,
                            direction=signal.direction,
                            state_before="IDLE",
                            state_after="WAITING_LEG",
                            metadata={
                                "s1_time": setup.s1.pivot_time,
                                "s1_price": setup.s1.price,
                                "s2_time": setup.s2.pivot_time,
                                "s2_price": setup.s2.price,
                            },
                        )
                    )
                    active = setup

            if active is not None and open_trade is None and pending_entry is None:
                active = self._advance_active_setup(
                    active,
                    new_pivots,
                    known_obs,
                    known_fvgs,
                    row,
                    bar_index,
                    events,
                )
                if active is not None and active.state == "ORDER_PENDING":
                    pending_entry = self._pending_from_setup(active, current_time, row, events, order_rows)
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
                    metadata={"pnl": trade_row["pnl"], "rr": trade_row["rr"]},
                )
            )
            if equity_rows:
                final_peak = max(peak_equity, balance)
                equity_rows[-1]["balance"] = balance
                equity_rows[-1]["equity"] = balance
                equity_rows[-1]["drawdown_abs"] = balance - final_peak
                equity_rows[-1]["drawdown_pct"] = (balance - final_peak) / final_peak if final_peak else 0.0
                equity_rows[-1]["open_positions"] = 0
            active = None
            open_trade = None

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
                )
            )
            active = None
            pending_entry = None

        trades = pd.DataFrame(trade_rows)
        orders = pd.DataFrame(order_rows)
        fills = pd.DataFrame(fill_rows)
        equity_curve = pd.DataFrame(equity_rows)
        metrics = summarize_trades(trades)
        metrics.update(self._event_counts(events, equity_curve))
        return BacktestResult(
            events=events,
            orders=orders,
            fills=fills,
            trades=trades,
            equity_curve=equity_curve,
            metrics=metrics,
        )

    def _run_gap_segments(self, gap_plan: CandleGapPlan) -> BacktestResult:
        initial_balance = float(self.params.execution.initial_balance)
        current_balance = initial_balance
        events: list[dict] = [
            self._event(
                "DATA_GAP_SKIPPED",
                gap.before,
                setup_id="data-gap",
                state_before="CONTINUOUS",
                state_after="RESET",
                metadata=gap.as_dict(),
            )
            for gap in gap_plan.gaps
        ]
        orders: list[pd.DataFrame] = []
        fills: list[pd.DataFrame] = []
        trades: list[pd.DataFrame] = []
        equities: list[pd.DataFrame] = []
        open_positions_reset = 0

        for segment_index, segment in enumerate(gap_plan.segments, start=1):
            result = self.run(segment.frame)
            prefix = f"seg{segment_index}-"
            events.extend(self._prefixed_events(result.events, prefix))
            orders.append(self._prefixed_frame(result.orders, prefix, setup_columns=("setup_id",), ref_columns=("order_ref",)))
            fills.append(self._prefixed_frame(result.fills, prefix, setup_columns=("setup_id",), ref_columns=("order_ref",)))
            trades.append(self._prefixed_frame(result.trades, prefix, setup_columns=("setup_id",), ref_columns=()))

            equity = result.equity_curve.copy()
            if not equity.empty:
                open_positions_reset += int((equity["open_positions"].iloc[-1] or 0) > 0)
                offset = current_balance - initial_balance
                equity["balance"] = pd.to_numeric(equity["balance"], errors="coerce") + offset
                equity["equity"] = pd.to_numeric(equity["equity"], errors="coerce") + offset
                current_balance = float(equity["balance"].iloc[-1])
                equities.append(equity)

        orders_frame = self._concat_frames(orders)
        fills_frame = self._concat_frames(fills)
        trades_frame = self._concat_frames(trades)
        equity_curve = self._recalculate_combined_equity(self._concat_frames(equities))
        events = sorted(events, key=lambda event: pd.Timestamp(event["event_time"]) if event.get("event_time") else pd.Timestamp.min)

        metrics = summarize_trades(trades_frame)
        metrics.update(self._event_counts(events, equity_curve))
        metrics.update(
            {
                "data_gap_count": len(gap_plan.gaps),
                "data_gap_missing_candles": gap_plan.missing_candles,
                "data_gap_segments_used": len(gap_plan.segments),
                "data_gap_segments_dropped": len(gap_plan.dropped_segments),
                "data_gap_dropped_rows": gap_plan.dropped_rows,
                "data_gap_open_positions_reset": open_positions_reset,
                "data_gap_policy": {
                    "timeframe": gap_plan.timeframe,
                    "expected_delta_seconds": gap_plan.expected_delta_seconds,
                    "min_segment_rows": self.gap_min_segment_rows,
                    "gaps": [gap.as_dict() for gap in gap_plan.gaps[:50]],
                    "dropped_segments": [segment.as_dict() for segment in gap_plan.dropped_segments[:50]],
                },
            }
        )
        return BacktestResult(
            events=events,
            orders=orders_frame,
            fills=fills_frame,
            trades=trades_frame,
            equity_curve=equity_curve,
            metrics=metrics,
        )

    def _prefixed_events(self, events: list[dict], prefix: str) -> list[dict]:
        prefixed = []
        for event in events:
            row = dict(event)
            setup_id = row.get("setup_id")
            if setup_id and setup_id != "market":
                row["setup_id"] = f"{prefix}{setup_id}"
            prefixed.append(row)
        return prefixed

    def _prefixed_frame(
        self,
        frame: pd.DataFrame,
        prefix: str,
        setup_columns: tuple[str, ...],
        ref_columns: tuple[str, ...],
    ) -> pd.DataFrame:
        if frame.empty:
            return frame
        out = frame.copy()
        for column in setup_columns:
            if column in out:
                out[column] = out[column].map(lambda value: f"{prefix}{value}" if pd.notna(value) else value)
        for column in ref_columns:
            if column in out:
                out[column] = out[column].map(lambda value: f"{prefix}{value}" if pd.notna(value) else value)
        return out

    def _concat_frames(self, frames: list[pd.DataFrame]) -> pd.DataFrame:
        usable = [frame for frame in frames if not frame.empty]
        return pd.concat(usable, ignore_index=True) if usable else pd.DataFrame()

    def _recalculate_combined_equity(self, equity_curve: pd.DataFrame) -> pd.DataFrame:
        if equity_curve.empty:
            return equity_curve
        out = equity_curve.sort_values("time").reset_index(drop=True).copy()
        equity = pd.to_numeric(out["equity"], errors="coerce")
        peak = equity.cummax()
        out["drawdown_abs"] = equity - peak
        out["drawdown_pct"] = out["drawdown_abs"] / peak.where(peak != 0)
        return out

    def _h1_signal_events(self, h1: pd.DataFrame) -> list[H1SignalEvent]:
        events: list[H1SignalEvent] = []
        if len(h1) < 2:
            return events
        delta = timeframe_delta("H1")
        for idx in range(1, len(h1)):
            c1 = h1.iloc[idx - 1].to_dict()
            c2 = h1.iloc[idx].to_dict()
            signal = crt_signal(c1, c2, detect_c3=self.params.detect_c3, model=self.params.crt_model)
            if signal is None:
                continue
            c2_time = pd.Timestamp(h1.iloc[idx]["time_open"])
            events.append(
                H1SignalEvent(
                    available_time=c2_time + delta,
                    c2_time=c2_time,
                    direction=signal.direction,
                    c1_high=signal.c1_high,
                    c1_low=signal.c1_low,
                    c1_mid=signal.c1_mid,
                    c2_close=signal.c2_close,
                    is_c3=signal.is_c3,
                )
            )
        return events

    def _build_setup_from_signal(
        self,
        setup_id: str,
        signal: H1SignalEvent,
        previous_high: Pivot | None,
        latest_high: Pivot | None,
        previous_low: Pivot | None,
        latest_low: Pivot | None,
    ) -> ActiveSetup | None:
        if signal.direction == "bearish":
            if previous_high is None or latest_high is None:
                return None
            if self.params.strategy_mode == "C_S2_INSIDE_S1" and latest_high.price >= previous_high.price:
                return None
            return ActiveSetup(
                setup_id=setup_id,
                direction="bearish",
                s1=previous_high,
                s2=latest_high,
                c1_high=signal.c1_high,
                c1_low=signal.c1_low,
                tp=signal.c1_low,
            )
        if previous_low is None or latest_low is None:
            return None
        if self.params.strategy_mode == "C_S2_INSIDE_S1" and latest_low.price <= previous_low.price:
            return None
        return ActiveSetup(
            setup_id=setup_id,
            direction="bullish",
            s1=previous_low,
            s2=latest_low,
            c1_high=signal.c1_high,
            c1_low=signal.c1_low,
            tp=signal.c1_high,
        )

    def _advance_active_setup(
        self,
        active: ActiveSetup,
        new_pivots: list[Pivot],
        known_obs: list[PriceZone],
        known_fvgs: list[PriceZone],
        row: pd.Series,
        bar_index: int,
        events: list[dict],
    ) -> ActiveSetup | None:
        current_time = pd.Timestamp(row["time_open"])
        if self.params.strategy_mode in {"A_INVALIDATION_S2", "C_S2_INSIDE_S1"}:
            if s2_invalidated(active.direction, float(row["close"]), active.s2.price):
                events.append(
                    self._event(
                        "SETUP_INVALIDATED_S2",
                        current_time,
                        setup_id=active.setup_id,
                        direction=active.direction,
                        price=float(row["close"]),
                        state_before=active.state,
                        state_after="INVALIDATED",
                        metadata={"s2_price": active.s2.price},
                    )
                )
                return None

        if active.state == "WAITING_LEG":
            wanted_kind = "low" if active.direction == "bearish" else "high"
            for pivot in new_pivots:
                if pivot.kind == wanted_kind and pivot.pivot_time > active.s2.pivot_time:
                    active.leg_end = pivot
                    active.ote_bottom, active.ote_top = ote_zone(active.s2.price, pivot.price, self.params.ote_deep)
                    active.state = "WAITING_PD_ARRAY"
                    events.append(
                        self._event(
                            "LEG_FOUND",
                            pivot.confirmation_time,
                            setup_id=active.setup_id,
                            direction=active.direction,
                            price=pivot.price,
                            state_before="WAITING_LEG",
                            state_after="WAITING_PD_ARRAY",
                            metadata={"leg_end_time": pivot.pivot_time},
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
                            metadata={"ote_bottom": active.ote_bottom, "ote_top": active.ote_top},
                        )
                    )
                    break

        if active.state == "WAITING_PD_ARRAY":
            assert active.leg_end is not None
            assert active.ote_bottom is not None
            assert active.ote_top is not None
            selected = select_pd_array(
                known_obs,
                known_fvgs,
                active.direction,
                active.s2.pivot_time,
                active.leg_end.pivot_time,
                active.ote_bottom,
                active.ote_top,
                pd_mode=self.params.pd_mode,
                require_midpoint=self.params.pd_require_mid_in_ote,
            )
            if selected is not None:
                active.pd_zone = selected
                active.state = "WAITING_MITIGATION"
                event_type = "OB_SELECTED" if selected.kind == "OB" else "FVG_SELECTED"
                events.append(
                    self._event(
                        event_type,
                        current_time,
                        setup_id=active.setup_id,
                        direction=active.direction,
                        price=selected.midpoint,
                        state_before="WAITING_PD_ARRAY",
                        state_after="WAITING_MITIGATION",
                        metadata={
                            "pd_time": selected.source_time,
                            "pd_top": selected.top,
                            "pd_bottom": selected.bottom,
                            "pd_mid": selected.midpoint,
                        },
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
                    )
                )

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
                    )
                )
                active.state = "ORDER_PENDING"
        return active

    def _pending_from_setup(
        self,
        active: ActiveSetup,
        current_time: pd.Timestamp,
        row: pd.Series,
        events: list[dict],
        order_rows: list[dict],
    ) -> PendingEntry | None:
        assert active.pd_zone is not None
        entry_price = float(row["close"])
        if active.direction == "bullish":
            sl = active.pd_zone.bottom - self.params.sl_buffer_ticks * self.tick_size
        else:
            sl = active.pd_zone.top + self.params.sl_buffer_ticks * self.tick_size
        tp = active.tp
        if not risk_is_valid(active.direction, entry_price, sl, tp):
            events.append(
                self._event(
                    "RISK_REJECTED",
                    current_time,
                    setup_id=active.setup_id,
                    direction=active.direction,
                    price=entry_price,
                    state_before="ORDER_PENDING",
                    state_after="INVALIDATED",
                    metadata={"sl": sl, "tp": tp},
                )
            )
            return None
        order_ref = f"{active.setup_id}-entry"
        pending = PendingEntry(
            order_ref=order_ref,
            setup=active,
            direction=active.direction,
            requested_time=current_time,
            volume=self.params.execution.order_qty,
            sl=sl,
            tp=tp,
            pd_type=active.pd_zone.kind,
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
                metadata={
                    "fill_policy": self.params.execution.fill_policy,
                    "sl": sl,
                    "tp": tp,
                    "pd_type": active.pd_zone.kind,
                },
            )
        )
        active.state = "ORDER_PENDING"
        return pending

    def _open_trade(
        self,
        pending: PendingEntry,
        entry_time: pd.Timestamp,
        entry_index: int,
        entry_price: float,
        events: list[dict],
        order_rows: list[dict],
        fill_rows: list[dict],
    ) -> OpenTrade:
        risk = abs(entry_price - pending.sl)
        reward = abs(pending.tp - entry_price)
        rr = reward / risk if risk else 0.0
        self._ensure_order_row(pending, entry_price, order_rows)
        self._set_order_status(order_rows, pending.order_ref, "filled")
        fill_rows.append(
            {
                "order_ref": pending.order_ref,
                "setup_id": pending.setup.setup_id,
                "fill_time": entry_time,
                "fill_price": entry_price,
                "volume": pending.volume,
                "commission": self.params.execution.commission_per_trade,
                "slippage": self.params.execution.slippage_ticks * self.tick_size,
                "metadata": {"fill_policy": self.params.execution.fill_policy, **pending.metadata},
            }
        )
        events.append(
            self._event(
                "TRADE_OPENED",
                entry_time,
                setup_id=pending.setup.setup_id,
                direction=pending.direction,
                price=entry_price,
                state_before="ORDER_PENDING",
                state_after="IN_POSITION",
                metadata={"sl": pending.sl, "tp": pending.tp, "rr": rr, "pd_type": pending.pd_type, **pending.metadata},
            )
        )
        return OpenTrade(
            order_ref=pending.order_ref,
            setup_id=pending.setup.setup_id,
            direction=pending.direction,
            entry_time=entry_time,
            entry_index=entry_index,
            entry_price=entry_price,
            volume=pending.volume,
            sl=pending.sl,
            tp=pending.tp,
            pd_type=pending.pd_type,
            strategy_mode=self.params.strategy_mode,
            rr=rr,
            metadata=pending.metadata,
        )

    def _ensure_order_row(self, pending: PendingEntry, requested_price: float, order_rows: list[dict]) -> None:
        if any(row["order_ref"] == pending.order_ref for row in order_rows):
            return
        order_rows.append(
            {
                "order_ref": pending.order_ref,
                "setup_id": pending.setup.setup_id,
                "order_type": "market",
                "direction": pending.direction,
                "requested_time": pending.requested_time,
                "requested_price": requested_price,
                "volume": pending.volume,
                "sl": pending.sl,
                "tp": pending.tp,
                "status": "created",
                "external_order_id": None,
                "metadata": {
                    "pd_type": pending.pd_type,
                    "fill_policy": self.params.execution.fill_policy,
                    **pending.metadata,
                },
            }
        )

    def _set_order_status(self, order_rows: list[dict], order_ref: str, status: str) -> None:
        for row in reversed(order_rows):
            if row["order_ref"] == order_ref:
                row["status"] = status
                return

    def _close_trade(
        self,
        trade: OpenTrade,
        exit_time: pd.Timestamp,
        exit_price: float,
        reason: str,
    ) -> dict:
        points = pnl_points(trade.direction, trade.entry_price, exit_price)
        pnl = points * trade.volume - self.params.execution.commission_per_trade
        risk = abs(trade.entry_price - trade.sl)
        realized_rr = points / risk if risk else 0.0
        return {
            "setup_id": trade.setup_id,
            "direction": trade.direction,
            "entry_time": trade.entry_time,
            "entry_price": trade.entry_price,
            "exit_time": exit_time,
            "exit_price": exit_price,
            "volume": trade.volume,
            "sl": trade.sl,
            "tp": trade.tp,
            "exit_reason": reason,
            "pnl": pnl,
            "pnl_points": points,
            "rr": realized_rr,
            "pd_type": trade.pd_type,
            "strategy_mode": trade.strategy_mode,
            "session_name": None,
            "metadata": trade.metadata,
        }

    def _event(
        self,
        event_type: str,
        event_time: datetime | pd.Timestamp,
        setup_id: str,
        direction: str | None = None,
        price: float | None = None,
        state_before: str | None = None,
        state_after: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        return {
            "setup_id": setup_id,
            "event_type": event_type,
            "event_time": pd.Timestamp(event_time) if event_time is not None else None,
            "direction": direction,
            "price": price,
            "state_before": state_before,
            "state_after": state_after,
            "metadata": metadata or {},
        }

    def _trade_direction_allowed(self, direction: str) -> bool:
        if self.params.trade_direction == "bullish_only":
            return direction == "bullish"
        if self.params.trade_direction == "bearish_only":
            return direction == "bearish"
        return True

    def _in_killzone(self, timestamp: pd.Timestamp) -> bool:
        local = pd.Timestamp(timestamp).tz_convert(self.params.timezone)
        minute_of_day = local.hour * 60 + local.minute
        for killzone in self.params.killzones.values():
            if killzone.start_hour * 60 <= minute_of_day < killzone.end_hour * 60:
                return True
        return False

    def _event_counts(self, events: list[dict], equity_curve: pd.DataFrame) -> dict:
        event_types = pd.Series([event["event_type"] for event in events])
        max_drawdown_abs = None
        max_drawdown_pct = None
        if not equity_curve.empty:
            max_drawdown_abs = float(equity_curve["drawdown_abs"].min())
            max_drawdown_pct = float(equity_curve["drawdown_pct"].min())
        return {
            "total_h1_signals": int((event_types == "H1_SIGNAL").sum()) if not event_types.empty else 0,
            "total_setups": int((event_types == "M15_DOUBLE_SWING_VALIDATED").sum()) if not event_types.empty else 0,
            "total_legs": int((event_types == "LEG_FOUND").sum()) if not event_types.empty else 0,
            "total_pd_selected": int(event_types.isin(["OB_SELECTED", "FVG_SELECTED"]).sum()) if not event_types.empty else 0,
            "total_pd_touched": int((event_types == "PD_TOUCHED").sum()) if not event_types.empty else 0,
            "total_rejections": int((event_types == "REJECTION_CONFIRMED").sum()) if not event_types.empty else 0,
            "total_risk_rejected": int((event_types == "RISK_REJECTED").sum()) if not event_types.empty else 0,
            "max_drawdown_abs": max_drawdown_abs,
            "max_drawdown_pct": max_drawdown_pct,
        }
