from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import Engine, text

from ict.data.gaps import split_continuous_candles
from ict.data.resample import resample_ohlcv
from ict.db.session import build_engine
from ict.strategy.indicators import fib_level, ote_zone


REVIEW_TIMEFRAMES = ("H4", "H1", "M30", "M15", "M5", "M1")
TIMEFRAME_WINDOWS = {
    "H4": (timedelta(days=10), timedelta(days=2)),
    "H1": (timedelta(days=5), timedelta(days=1)),
    "M30": (timedelta(days=2), timedelta(hours=12)),
    "M15": (timedelta(days=2), timedelta(hours=12)),
    "M5": (timedelta(hours=12), timedelta(hours=4)),
    "M1": (timedelta(hours=4), timedelta(hours=2)),
}
FIB_LEVELS = (0.0, 0.5, 0.618, 0.705, 0.79, 1.0)


class ReviewNotFoundError(LookupError):
    pass


def fetch_runs(limit: int = 100, engine: Engine | None = None) -> list[dict[str, Any]]:
    query = text(
        """
        SELECT
            r.id,
            r.status,
            r.run_type,
            r.start_time,
            r.end_time,
            r.created_at,
            r.metadata ->> 'launch_id' AS launch_id,
            r.metadata ->> 'launch_label' AS launch_label,
            r.initial_balance,
            r.final_balance,
            s.symbol_code,
            ds.name AS source_name,
            sv.name AS strategy_name,
            sv.version AS strategy_version,
            ps.name AS parameter_set_name,
            d.timeframe,
            COALESCE(rm.total_trades, 0) AS total_trades,
            rm.winrate,
            rm.avg_rr,
            rm.profit_factor,
            rm.net_profit,
            rm.max_drawdown_pct
        FROM backtest_runs r
        JOIN symbols s ON s.id = r.symbol_id
        JOIN data_sources ds ON ds.id = r.source_id
        JOIN strategy_versions sv ON sv.id = r.strategy_version_id
        JOIN parameter_sets ps ON ps.id = r.parameter_set_id
        JOIN datasets d ON d.id = r.dataset_id
        LEFT JOIN run_metrics rm ON rm.run_id = r.id
        ORDER BY r.created_at DESC
        LIMIT :limit
        """
    )
    with (engine or build_engine()).connect() as connection:
        rows = connection.execute(query, {"limit": int(limit)}).mappings().all()
    return [_sanitize_mapping(row) for row in rows]


def fetch_run_trades(run_id: str, engine: Engine | None = None) -> list[dict[str, Any]]:
    query = text(
        """
        SELECT
            t.id,
            t.run_id,
            t.setup_id,
            t.direction,
            t.entry_time,
            t.entry_price,
            t.exit_time,
            t.exit_price,
            t.volume,
            t.sl,
            t.tp,
            t.exit_reason,
            t.pnl,
            t.pnl_points,
            t.rr,
            t.mae,
            t.mfe,
            t.pd_type,
            t.strategy_mode,
            t.session_name,
            s.symbol_code,
            ds.name AS source_name
        FROM trades t
        JOIN symbols s ON s.id = t.symbol_id
        JOIN data_sources ds ON ds.id = t.source_id
        WHERE t.run_id = CAST(:run_id AS uuid)
        ORDER BY t.entry_time
        """
    )
    with (engine or build_engine()).connect() as connection:
        rows = connection.execute(query, {"run_id": run_id}).mappings().all()
    return [_sanitize_mapping(row) for row in rows]


def build_trade_review(trade_id: str, engine: Engine | None = None) -> dict[str, Any]:
    engine = engine or build_engine()
    trade = _fetch_trade_context(trade_id, engine)
    entry_time = _as_utc_timestamp(trade["entry_time"])
    exit_time = _as_utc_timestamp(trade["exit_time"]) if trade.get("exit_time") else entry_time + pd.Timedelta(hours=2)
    global_start = max(entry_time - pd.Timedelta(days=10), _as_utc_timestamp(trade["run_start_time"]))
    global_end = min(exit_time + pd.Timedelta(days=2), _as_utc_timestamp(trade["run_end_time"]))

    candles = _fetch_m1_candles(trade, global_start, global_end, engine)
    events = _fetch_events(trade, global_start, global_end, engine)
    timeframes = _build_timeframe_payloads(candles, entry_time, exit_time)
    fib = _infer_fib(events, trade, candles)
    risk_reward = _risk_reward_payload(trade)

    return {
        "trade": _public_trade(trade),
        "run": _public_run(trade),
        "symbol": {
            "id": str(trade["symbol_id"]),
            "code": trade["symbol_code"],
            "asset_type": trade["asset_type"],
        },
        "source": {
            "id": str(trade["source_id"]),
            "name": trade["source_name"],
            "type": trade["source_type"],
        },
        "dataset": {
            "id": str(trade["dataset_id"]),
            "timeframe": trade["dataset_timeframe"],
            "start_time": _iso(trade["dataset_start_time"]),
            "end_time": _iso(trade["dataset_end_time"]),
        },
        "timeframes": timeframes,
        "events": [_event_payload(event) for event in events],
        "markers": _marker_payload(trade, events),
        "annotations": _annotation_payload(trade, events, fib),
        "fib": fib,
        "risk_reward": risk_reward,
        "quality": _review_quality(timeframes),
    }


def _fetch_trade_context(trade_id: str, engine: Engine) -> dict[str, Any]:
    query = text(
        """
        SELECT
            t.id,
            t.run_id,
            t.dataset_id,
            t.setup_id,
            t.symbol_id,
            t.source_id,
            t.direction,
            t.entry_time,
            t.entry_price,
            t.exit_time,
            t.exit_price,
            t.volume,
            t.sl,
            t.tp,
            t.exit_reason,
            t.pnl,
            t.pnl_points,
            t.rr,
            t.mae,
            t.mfe,
            t.pd_type,
            t.strategy_mode,
            t.session_name,
            t.metadata,
            r.status AS run_status,
            r.run_type,
            r.start_time AS run_start_time,
            r.end_time AS run_end_time,
            r.initial_balance,
            r.final_balance,
            r.created_at AS run_created_at,
            sv.name AS strategy_name,
            sv.version AS strategy_version,
            ps.name AS parameter_set_name,
            d.timeframe AS dataset_timeframe,
            d.start_time AS dataset_start_time,
            d.end_time AS dataset_end_time,
            s.symbol_code,
            s.asset_type,
            ds.name AS source_name,
            ds.source_type
        FROM trades t
        JOIN backtest_runs r ON r.id = t.run_id
        JOIN strategy_versions sv ON sv.id = r.strategy_version_id
        JOIN parameter_sets ps ON ps.id = r.parameter_set_id
        JOIN datasets d ON d.id = t.dataset_id
        JOIN symbols s ON s.id = t.symbol_id
        JOIN data_sources ds ON ds.id = t.source_id
        WHERE t.id = CAST(:trade_id AS uuid)
        """
    )
    with engine.connect() as connection:
        row = connection.execute(query, {"trade_id": trade_id}).mappings().one_or_none()
    if row is None:
        raise ReviewNotFoundError(f"Trade not found: {trade_id}")
    return dict(row)


def _fetch_m1_candles(
    trade: dict[str, Any],
    start_time: pd.Timestamp,
    end_time: pd.Timestamp,
    engine: Engine,
) -> pd.DataFrame:
    query = text(
        """
        SELECT
            time_open,
            open,
            high,
            low,
            close,
            COALESCE(tick_volume, 0) AS tick_volume,
            COALESCE(real_volume, 0) AS real_volume,
            COALESCE(spread, 0) AS spread
        FROM market_candles
        WHERE symbol_id = :symbol_id
            AND source_id = :source_id
            AND timeframe = 'M1'
            AND time_open >= :start_time
            AND time_open <= :end_time
        ORDER BY time_open
        """
    )
    with engine.connect() as connection:
        rows = connection.execute(
            query,
            {
                "symbol_id": trade["symbol_id"],
                "source_id": trade["source_id"],
                "start_time": start_time.to_pydatetime(),
                "end_time": end_time.to_pydatetime(),
            },
        ).mappings().all()
    return pd.DataFrame([dict(row) for row in rows])


def _fetch_events(
    trade: dict[str, Any],
    start_time: pd.Timestamp,
    end_time: pd.Timestamp,
    engine: Engine,
) -> list[dict[str, Any]]:
    query = text(
        """
        SELECT
            id,
            setup_id,
            event_type,
            event_time,
            direction,
            price,
            state_before,
            state_after,
            metadata
        FROM setup_events
        WHERE run_id = :run_id
            AND event_time >= :start_time
            AND event_time <= :end_time
            AND (setup_id = :setup_id OR setup_id = 'market')
        ORDER BY event_time, event_type
        """
    )
    with engine.connect() as connection:
        rows = connection.execute(
            query,
            {
                "run_id": trade["run_id"],
                "setup_id": trade["setup_id"],
                "start_time": start_time.to_pydatetime(),
                "end_time": end_time.to_pydatetime(),
            },
        ).mappings().all()
    return [dict(row) for row in rows]


def _build_timeframe_payloads(
    m1_candles: pd.DataFrame,
    entry_time: pd.Timestamp,
    exit_time: pd.Timestamp,
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    if m1_candles.empty:
        for timeframe in REVIEW_TIMEFRAMES:
            before, after = TIMEFRAME_WINDOWS[timeframe]
            output[timeframe] = {
                "timeframe": timeframe,
                "window_start": _iso(entry_time - before),
                "window_end": _iso(exit_time + after),
                "candles": [],
                "gap_summary": _empty_gap_summary(),
            }
        return output

    normalized = m1_candles.copy()
    normalized["time_open"] = pd.to_datetime(normalized["time_open"], utc=True)
    for timeframe in REVIEW_TIMEFRAMES:
        before, after = TIMEFRAME_WINDOWS[timeframe]
        window_start = entry_time - before
        window_end = exit_time + after
        resampled = resample_ohlcv(normalized, timeframe)
        resampled["time_open"] = pd.to_datetime(resampled["time_open"], utc=True)
        visible = resampled[(resampled["time_open"] >= window_start) & (resampled["time_open"] <= window_end)]
        m1_visible = normalized[(normalized["time_open"] >= window_start) & (normalized["time_open"] <= window_end)]
        output[timeframe] = {
            "timeframe": timeframe,
            "window_start": _iso(window_start),
            "window_end": _iso(window_end),
            "candles": [_candle_payload(row) for row in visible.to_dict(orient="records")],
            "gap_summary": _gap_summary(m1_visible),
        }
    return output


def _infer_fib(events: list[dict[str, Any]], trade: dict[str, Any], candles: pd.DataFrame) -> dict[str, Any]:
    direction = str(trade["direction"])
    double_swing = _first_event(events, "M15_DOUBLE_SWING_VALIDATED")
    leg = _first_event(events, "LEG_FOUND")
    ote = _first_event(events, "OTE_CREATED")

    start_time = None
    end_time = None
    start_price = None
    end_price = None
    source = "fallback"

    if double_swing and leg:
        double_meta = _dict_value(double_swing.get("metadata"))
        leg_meta = _dict_value(leg.get("metadata"))
        start_time = double_meta.get("s2_time")
        start_price = _optional_float(double_meta.get("s2_price"))
        end_time = leg_meta.get("leg_end_time") or leg.get("event_time")
        end_price = _optional_float(leg.get("price"))
        source = "events"

    if start_price is None or end_price is None:
        fallback = _fallback_fib_anchors(trade, candles)
        start_time = fallback["start_time"]
        start_price = fallback["start_price"]
        end_time = fallback["end_time"]
        end_price = fallback["end_price"]
        source = fallback["source"]

    if start_price is None or end_price is None:
        return {
            "available": False,
            "source": "unavailable",
            "direction": direction,
            "visible_timeframes": [],
            "levels": [],
            "ote_zone": None,
        }

    ote_meta = _dict_value(ote.get("metadata")) if ote else {}
    ote_bottom = _optional_float(ote_meta.get("ote_bottom"))
    ote_top = _optional_float(ote_meta.get("ote_top"))
    if ote_bottom is None or ote_top is None:
        ote_bottom, ote_top = ote_zone(float(start_price), float(end_price))

    levels = [
        {
            "level": level,
            "label": _fib_label(level),
            "price": fib_level(float(start_price), float(end_price), level),
        }
        for level in FIB_LEVELS
    ]
    return {
        "available": True,
        "source": source,
        "direction": direction,
        "visible_timeframes": ["M15", "M5", "M1"] if source == "events" else ["M1"],
        "anchor_start": {"time": _iso(start_time), "price": float(start_price)},
        "anchor_end": {"time": _iso(end_time), "price": float(end_price)},
        "levels": levels,
        "ote_zone": {
            "bottom": min(float(ote_bottom), float(ote_top)),
            "top": max(float(ote_bottom), float(ote_top)),
        },
    }


def _fallback_fib_anchors(trade: dict[str, Any], candles: pd.DataFrame) -> dict[str, Any]:
    if candles.empty:
        return {"source": "unavailable", "start_time": None, "start_price": None, "end_time": None, "end_price": None}

    entry_time = _as_utc_timestamp(trade["entry_time"])
    start = entry_time - pd.Timedelta(hours=12)
    end = entry_time + pd.Timedelta(minutes=30)
    frame = candles.copy()
    frame["time_open"] = pd.to_datetime(frame["time_open"], utc=True)
    frame = frame[(frame["time_open"] >= start) & (frame["time_open"] <= end)]
    if frame.empty:
        frame = candles.copy()
        frame["time_open"] = pd.to_datetime(frame["time_open"], utc=True)

    if trade["direction"] == "bearish":
        start_idx = frame["high"].astype(float).idxmax()
        end_idx = frame["low"].astype(float).idxmin()
        if frame.loc[start_idx, "time_open"] > frame.loc[end_idx, "time_open"]:
            start_idx, end_idx = end_idx, start_idx
        start_price = float(frame.loc[start_idx, "high"])
        end_price = float(frame.loc[end_idx, "low"])
    else:
        start_idx = frame["low"].astype(float).idxmin()
        end_idx = frame["high"].astype(float).idxmax()
        if frame.loc[start_idx, "time_open"] > frame.loc[end_idx, "time_open"]:
            start_idx, end_idx = end_idx, start_idx
        start_price = float(frame.loc[start_idx, "low"])
        end_price = float(frame.loc[end_idx, "high"])

    return {
        "source": "visible_swing_fallback",
        "start_time": frame.loc[start_idx, "time_open"],
        "start_price": start_price,
        "end_time": frame.loc[end_idx, "time_open"],
        "end_price": end_price,
    }


def _risk_reward_payload(trade: dict[str, Any]) -> dict[str, Any] | None:
    entry = _optional_float(trade.get("entry_price"))
    sl = _optional_float(trade.get("sl"))
    tp = _optional_float(trade.get("tp"))
    exit_price = _optional_float(trade.get("exit_price"))
    if entry is None or sl is None or tp is None:
        return None
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "exit": exit_price,
        "risk": risk,
        "reward": reward,
        "planned_rr": reward / risk if risk else None,
        "realized_rr": _optional_float(trade.get("rr")),
        "risk_zone": {"bottom": min(entry, sl), "top": max(entry, sl)},
        "reward_zone": {"bottom": min(entry, tp), "top": max(entry, tp)},
    }


def _gap_summary(candles: pd.DataFrame) -> dict[str, Any]:
    if candles.empty:
        return _empty_gap_summary()
    plan = split_continuous_candles(candles, "M1", min_segment_rows=1)
    gaps = [gap.as_dict() for gap in plan.gaps]
    largest = max((gap["missing_candles"] for gap in gaps), default=0)
    return {
        "gap_count": len(gaps),
        "missing_candles": int(plan.missing_candles),
        "largest_gap_candles": int(largest),
        "gaps": gaps[:50],
    }


def _empty_gap_summary() -> dict[str, Any]:
    return {"gap_count": 0, "missing_candles": 0, "largest_gap_candles": 0, "gaps": []}


def _review_quality(timeframes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    total_missing = sum(int(payload["gap_summary"]["missing_candles"]) for payload in timeframes.values())
    max_gap = max((int(payload["gap_summary"]["largest_gap_candles"]) for payload in timeframes.values()), default=0)
    return {
        "has_gaps": total_missing > 0,
        "missing_m1_candles_across_windows": total_missing,
        "largest_gap_candles": max_gap,
    }


def _marker_payload(trade: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    markers = [
        {
            "kind": "entry",
            "time": _iso(trade["entry_time"]),
            "price": _optional_float(trade["entry_price"]),
            "direction": trade["direction"],
            "label": "ENTRY",
        }
    ]
    if trade.get("exit_time") and trade.get("exit_price"):
        markers.append(
            {
                "kind": "exit",
                "time": _iso(trade["exit_time"]),
                "price": _optional_float(trade["exit_price"]),
                "direction": trade["direction"],
                "label": "EXIT",
            }
        )
    for event in events:
        price = _optional_float(event.get("price"))
        if price is None:
            continue
        markers.append(
            {
                "kind": "event",
                "event_type": event["event_type"],
                "time": _iso(event["event_time"]),
                "price": price,
                "direction": event.get("direction"),
                "label": event["event_type"],
            }
        )
    return markers


def _annotation_payload(trade: dict[str, Any], events: list[dict[str, Any]], fib: dict[str, Any]) -> dict[str, Any]:
    entry_time = _iso(trade["entry_time"])
    exit_time = _iso(trade["exit_time"]) if trade.get("exit_time") else entry_time
    zones = []
    swings = []
    levels = []

    double_swing = _first_event(events, "M15_DOUBLE_SWING_VALIDATED")
    if double_swing:
        meta = _dict_value(double_swing.get("metadata"))
        for key, label in (("s1", "S1"), ("s2", "S2")):
            swing_time = meta.get(f"{key}_time")
            swing_price = _optional_float(meta.get(f"{key}_price"))
            if swing_time is not None and swing_price is not None:
                swings.append(
                    {
                        "id": f"{double_swing['setup_id']}-{label}",
                        "label": label,
                        "kind": "swing",
                        "time": _iso(swing_time),
                        "price": swing_price,
                        "direction": double_swing.get("direction"),
                        "visible_timeframes": ["M15"],
                    }
                )

    leg = _first_event(events, "LEG_FOUND")
    if leg:
        leg_meta = _dict_value(leg.get("metadata"))
        leg_time = leg_meta.get("leg_end_time") or leg.get("event_time")
        leg_price = _optional_float(leg.get("price"))
        if leg_time is not None and leg_price is not None:
            swings.append(
                {
                    "id": f"{leg['setup_id']}-LEG",
                    "label": "LEG",
                    "kind": "leg",
                    "time": _iso(leg_time),
                    "price": leg_price,
                    "direction": leg.get("direction"),
                    "visible_timeframes": ["M15", "M5"],
                }
            )

    selected_pd = _first_event(events, "OB_SELECTED") or _first_event(events, "FVG_SELECTED")
    if selected_pd:
        meta = _dict_value(selected_pd.get("metadata"))
        bottom = _optional_float(meta.get("pd_bottom"))
        top = _optional_float(meta.get("pd_top"))
        source_time = meta.get("pd_time") or selected_pd.get("event_time")
        if bottom is not None and top is not None and source_time is not None:
            zones.append(
                {
                    "id": f"{selected_pd['setup_id']}-{selected_pd['event_type']}",
                    "kind": "OB" if selected_pd["event_type"] == "OB_SELECTED" else "FVG",
                    "label": "OB" if selected_pd["event_type"] == "OB_SELECTED" else "FVG",
                    "direction": selected_pd.get("direction"),
                    "start_time": _iso(source_time),
                    "end_time": entry_time,
                    "bottom": min(bottom, top),
                    "top": max(bottom, top),
                    "mid": _optional_float(meta.get("pd_mid")),
                    "visible_timeframes": ["M15", "M5", "M1"],
                }
            )

    selected_ir = _first_event(events, "IMMEDIATE_REBALANCE_SELECTED")
    if selected_ir:
        meta = _dict_value(selected_ir.get("metadata"))
        bottom = _optional_float(meta.get("pd_bottom"))
        top = _optional_float(meta.get("pd_top"))
        origin_time = meta.get("ir_origin_time") or selected_ir.get("event_time")
        if bottom is not None and top is not None and origin_time is not None:
            zones.append(
                {
                    "id": f"{selected_ir['setup_id']}-IR",
                    "kind": "IR",
                    "label": "IR",
                    "direction": selected_ir.get("direction"),
                    "start_time": _iso(origin_time),
                    "end_time": entry_time,
                    "bottom": min(bottom, top),
                    "top": max(bottom, top),
                    "mid": _optional_float(meta.get("pd_mid")),
                    "visible_timeframes": ["M1", "M5"],
                }
            )

    if fib.get("available") and fib.get("ote_zone") and fib.get("anchor_start") and fib.get("anchor_end"):
        zones.append(
            {
                "id": f"{trade['setup_id']}-OTE",
                "kind": "OTE",
                "label": "OTE",
                "direction": trade.get("direction"),
                "start_time": fib["anchor_start"].get("time"),
                "end_time": entry_time,
                "bottom": fib["ote_zone"]["bottom"],
                "top": fib["ote_zone"]["top"],
                "mid": None,
                "visible_timeframes": fib.get("visible_timeframes") or ["M1"],
            }
        )

    h1_signal = _first_event(events, "H1_SIGNAL")
    h1_meta = _dict_value(h1_signal.get("metadata")) if h1_signal else {}
    c1_high = _optional_float(h1_meta.get("c1_high"))
    c1_low = _optional_float(h1_meta.get("c1_low"))
    c1_mid = _optional_float(h1_meta.get("c1_mid"))
    start_time = _iso(h1_meta.get("c2_time") or trade.get("entry_time"))
    target = _optional_float(trade.get("tp"))
    for label, price, role in (
        ("CRT HIGH", c1_high, "crt_high"),
        ("CRT MID", c1_mid, "crt_mid"),
        ("CRT LOW", c1_low, "crt_low"),
        ("CRT OBJ", target, "target"),
    ):
        if price is not None:
            levels.append(
                {
                    "id": f"{trade['setup_id']}-{role}",
                    "label": label,
                    "kind": role,
                    "price": price,
                    "start_time": start_time,
                    "end_time": exit_time,
                    "visible_timeframes": ["H1", "M30", "M15", "M5", "M1"] if role != "target" else ["M1", "M5", "M15"],
                }
            )

    trade_meta = _dict_value(trade.get("metadata"))
    target_candidates = trade_meta.get("target_candidates") if isinstance(trade_meta.get("target_candidates"), list) else []
    for index, candidate in enumerate(target_candidates[:12], start=1):
        if not isinstance(candidate, dict):
            continue
        price = _optional_float(candidate.get("price"))
        source = candidate.get("source")
        if price is None or not source:
            continue
        levels.append(
            {
                "id": f"{trade['setup_id']}-target-candidate-{index}",
                "label": str(source).upper(),
                "kind": "target_candidate",
                "price": price,
                "start_time": entry_time,
                "end_time": exit_time,
                "visible_timeframes": ["M1", "M5", "M15"],
            }
        )

    return {"zones": zones, "swings": swings, "levels": levels}


def _public_trade(trade: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "run_id",
        "dataset_id",
        "setup_id",
        "direction",
        "entry_time",
        "entry_price",
        "exit_time",
        "exit_price",
        "volume",
        "sl",
        "tp",
        "exit_reason",
        "pnl",
        "pnl_points",
        "rr",
        "mae",
        "mfe",
        "pd_type",
        "strategy_mode",
        "session_name",
        "metadata",
        "symbol_code",
        "source_name",
    )
    return {key: _json_value(trade.get(key)) for key in keys}


def _public_run(trade: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(trade["run_id"]),
        "status": trade["run_status"],
        "run_type": trade["run_type"],
        "strategy_name": trade["strategy_name"],
        "strategy_version": trade["strategy_version"],
        "parameter_set_name": trade["parameter_set_name"],
        "start_time": _iso(trade["run_start_time"]),
        "end_time": _iso(trade["run_end_time"]),
        "created_at": _iso(trade["run_created_at"]),
        "initial_balance": _optional_float(trade["initial_balance"]),
        "final_balance": _optional_float(trade["final_balance"]),
    }


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(event["id"]),
        "setup_id": event["setup_id"],
        "event_type": event["event_type"],
        "event_time": _iso(event["event_time"]),
        "direction": event.get("direction"),
        "price": _optional_float(event.get("price")),
        "state_before": event.get("state_before"),
        "state_after": event.get("state_after"),
        "metadata": _json_value(event.get("metadata") or {}),
    }


def _candle_payload(row: dict[str, Any]) -> dict[str, Any]:
    timestamp = _as_utc_timestamp(row["time_open"])
    return {
        "time": int(timestamp.timestamp()),
        "time_open": _iso(timestamp),
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
        "tick_volume": int(row.get("tick_volume") or 0),
        "real_volume": int(row.get("real_volume") or 0),
        "spread": float(row.get("spread") or 0),
    }


def _first_event(events: list[dict[str, Any]], event_type: str) -> dict[str, Any] | None:
    return next((event for event in events if event.get("event_type") == event_type), None)


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sanitize_mapping(row: Any) -> dict[str, Any]:
    return {key: _json_value(value) for key, value in dict(row).items()}


def _json_value(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, pd.Timestamp):
        return _iso(value)
    if isinstance(value, dict):
        return {str(key): _json_value(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(inner) for inner in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _as_utc_timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize(timezone.utc)
    return timestamp.tz_convert(timezone.utc)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return _as_utc_timestamp(value).isoformat()


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fib_label(level: float) -> str:
    if level in {0.0, 1.0}:
        return str(int(level))
    return f"{level:.3f}".rstrip("0").rstrip(".")
