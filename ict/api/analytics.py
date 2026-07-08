from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy import Engine, text

from ict.api.review import ReviewNotFoundError, _json_value, _sanitize_mapping
from ict.db.session import build_engine


def fetch_run_analytics(run_id: str, engine: Engine | None = None) -> dict[str, Any]:
    engine = engine or build_engine()
    run = _fetch_run_summary(run_id, engine)
    if run is None:
        raise ReviewNotFoundError(f"Run not found: {run_id}")
    trades = _fetch_trade_rows(run_id, engine)
    equity = _fetch_equity_rows(run_id, engine)
    event_counts = _fetch_event_counts(run_id, engine)
    return build_analytics_payload(run, trades, equity, event_counts)


def build_analytics_payload(
    run: dict[str, Any],
    trades: list[dict[str, Any]],
    equity: list[dict[str, Any]],
    event_counts: list[dict[str, Any]],
) -> dict[str, Any]:
    frame = _trade_frame(trades)
    summary = _summary_stats(frame)
    breakdowns = _build_breakdowns(frame)

    return {
        "run": _public_run(run),
        "summary": summary,
        "equity_curve": _equity_curve(equity, run, frame),
        "cumulative_pnl": _cumulative_pnl(frame),
        "monthly": _monthly_performance(frame),
        "breakdowns": breakdowns,
        "comparisons": _comparisons(frame),
        "rr_distribution": _rr_distribution(frame),
        "event_funnel": _event_funnel(event_counts),
        "diagnostics": _diagnostics(summary, breakdowns),
        "source": {
            "database": "local_canonical",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def _fetch_run_summary(run_id: str, engine: Engine) -> dict[str, Any] | None:
    query = text(
        """
        SELECT
            r.id AS run_id,
            r.status,
            r.run_type,
            r.start_time,
            r.end_time,
            r.created_at,
            r.initial_balance,
            r.final_balance,
            s.symbol_code,
            ds.name AS source_name,
            sv.name AS strategy_name,
            sv.version AS strategy_version,
            ps.name AS parameter_set_name,
            d.timeframe AS dataset_timeframe,
            COALESCE(rm.total_trades, 0) AS total_trades,
            COALESCE(rm.total_wins, 0) AS total_wins,
            COALESCE(rm.total_losses, 0) AS total_losses,
            rm.winrate,
            rm.avg_rr,
            rm.median_rr,
            rm.profit_factor,
            rm.expectancy,
            rm.net_profit,
            rm.max_drawdown_abs,
            rm.max_drawdown_pct,
            rm.max_consecutive_losses,
            rm.avg_trade_duration_seconds
        FROM backtest_runs r
        JOIN symbols s ON s.id = r.symbol_id
        JOIN data_sources ds ON ds.id = r.source_id
        JOIN strategy_versions sv ON sv.id = r.strategy_version_id
        JOIN parameter_sets ps ON ps.id = r.parameter_set_id
        JOIN datasets d ON d.id = r.dataset_id
        LEFT JOIN run_metrics rm ON rm.run_id = r.id
        WHERE r.id = CAST(:run_id AS uuid)
        """
    )
    with engine.connect() as connection:
        row = connection.execute(query, {"run_id": run_id}).mappings().one_or_none()
    return _sanitize_mapping(row) if row else None


def _fetch_trade_rows(run_id: str, engine: Engine) -> list[dict[str, Any]]:
    query = text(
        """
        SELECT
            id,
            direction,
            entry_time,
            exit_time,
            exit_reason,
            pnl,
            pnl_points,
            rr,
            mae,
            mfe,
            pd_type,
            strategy_mode,
            session_name,
            metadata
        FROM trades
        WHERE run_id = CAST(:run_id AS uuid)
        ORDER BY entry_time
        """
    )
    with engine.connect() as connection:
        rows = connection.execute(query, {"run_id": run_id}).mappings().all()
    return [_sanitize_mapping(row) for row in rows]


def _fetch_equity_rows(run_id: str, engine: Engine) -> list[dict[str, Any]]:
    query = text(
        """
        SELECT time, balance, equity, drawdown_abs, drawdown_pct, open_positions
        FROM equity_curve
        WHERE run_id = CAST(:run_id AS uuid)
        ORDER BY time
        """
    )
    with engine.connect() as connection:
        rows = connection.execute(query, {"run_id": run_id}).mappings().all()
    return [_sanitize_mapping(row) for row in rows]


def _fetch_event_counts(run_id: str, engine: Engine) -> list[dict[str, Any]]:
    query = text(
        """
        SELECT event_type, COUNT(*)::integer AS count
        FROM setup_events
        WHERE run_id = CAST(:run_id AS uuid)
        GROUP BY event_type
        ORDER BY count DESC, event_type
        """
    )
    with engine.connect() as connection:
        rows = connection.execute(query, {"run_id": run_id}).mappings().all()
    return [_sanitize_mapping(row) for row in rows]


def _trade_frame(trades: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(trades)
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "direction",
                "entry_time",
                "exit_time",
                "exit_reason",
                "pnl",
                "rr",
                "pd_type",
                "target_source",
                "strategy_mode",
                "session_name",
            ]
        )
    for column in ("pnl", "pnl_points", "rr", "mae", "mfe"):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in ("entry_time", "exit_time"):
        if column in frame:
            frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    frame["target_source"] = frame["metadata"].map(_target_source_from_metadata) if "metadata" in frame else "Unknown"
    return frame


def _target_source_from_metadata(metadata: Any) -> str:
    if not isinstance(metadata, dict):
        return "Unknown"
    source = metadata.get("target_source")
    if source:
        return str(source)
    model = metadata.get("target_model")
    if model:
        return str(model)
    return "Unknown"


def _summary_stats(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "winrate": None,
            "loss_rate": None,
            "net_pnl": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "profit_factor": None,
            "avg_rr": None,
            "median_rr": None,
            "expectancy": None,
            "best_trade": None,
            "worst_trade": None,
            "avg_win": None,
            "avg_loss": None,
            "payoff_ratio": None,
            "max_consecutive_losses": 0,
            "avg_duration_minutes": None,
        }
    pnl = pd.to_numeric(frame["pnl"], errors="coerce").fillna(0.0)
    rr = pd.to_numeric(frame["rr"], errors="coerce") if "rr" in frame else pd.Series(dtype=float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    total = int(len(frame))
    avg_win = _finite_float(wins.mean()) if len(wins) else None
    avg_loss = _finite_float(losses.mean()) if len(losses) else None
    gross_profit = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(losses.sum()) if len(losses) else 0.0

    durations = pd.Series(dtype=float)
    if {"entry_time", "exit_time"}.issubset(frame.columns):
        durations = (frame["exit_time"] - frame["entry_time"]).dt.total_seconds().dropna() / 60

    return {
        "total_trades": total,
        "wins": int((pnl > 0).sum()),
        "losses": int((pnl < 0).sum()),
        "breakeven": int((pnl == 0).sum()),
        "winrate": _ratio((pnl > 0).sum(), total),
        "loss_rate": _ratio((pnl < 0).sum(), total),
        "net_pnl": float(pnl.sum()),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": _profit_factor(gross_profit, gross_loss),
        "avg_rr": _finite_float(rr.mean()),
        "median_rr": _finite_float(rr.median()),
        "expectancy": _finite_float(pnl.mean()),
        "best_trade": _finite_float(pnl.max()),
        "worst_trade": _finite_float(pnl.min()),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": (avg_win / abs(avg_loss)) if avg_win is not None and avg_loss not in (None, 0) else None,
        "max_consecutive_losses": _max_consecutive_losses(pnl),
        "avg_duration_minutes": _finite_float(durations.mean()),
    }


def _build_breakdowns(frame: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    if frame.empty:
        return {
            "direction": [],
            "pd_type": [],
            "target_source": [],
            "session": [],
            "exit_reason": [],
            "hour_of_day": [],
            "day_of_week": [],
            "symbol": [],
            "source": [],
        }
    work = frame.copy()
    work["session"] = work.get("session_name", pd.Series(dtype=object)).fillna("Unknown")
    work["pd_type"] = work.get("pd_type", pd.Series(dtype=object)).fillna("Unknown")
    work["target_source"] = work.get("target_source", pd.Series(dtype=object)).fillna("Unknown")
    work["exit_reason"] = work.get("exit_reason", pd.Series(dtype=object)).fillna("Open/Unknown")
    work["hour_of_day"] = work["entry_time"].dt.hour.map(lambda value: f"{int(value):02d}:00" if pd.notna(value) else "Unknown")
    work["day_of_week"] = work["entry_time"].dt.day_name().fillna("Unknown")
    output = {
        "direction": _breakdown(work, "direction"),
        "pd_type": _breakdown(work, "pd_type"),
        "target_source": _breakdown(work, "target_source"),
        "session": _breakdown(work, "session"),
        "exit_reason": _breakdown(work, "exit_reason"),
        "hour_of_day": _breakdown(work, "hour_of_day"),
        "day_of_week": _breakdown(work, "day_of_week"),
    }
    if "symbol_code" in work:
        work["symbol"] = work["symbol_code"].fillna("Unknown")
        output["symbol"] = _breakdown(work, "symbol")
    else:
        output["symbol"] = []
    if "source_name" in work:
        work["source"] = work["source_name"].fillna("Unknown")
        output["source"] = _breakdown(work, "source")
    else:
        output["source"] = []
    return output


def _breakdown(frame: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    rows = []
    for name, group in frame.groupby(column, dropna=False):
        stats = _summary_stats(group)
        rows.append(
            {
                "name": str(name) if pd.notna(name) else "Unknown",
                "trades": stats["total_trades"],
                "pnl": stats["net_pnl"],
                "winrate": stats["winrate"],
                "avg_rr": stats["avg_rr"],
                "expectancy": stats["expectancy"],
                "profit_factor": stats["profit_factor"],
            }
        )
    return sorted(rows, key=lambda row: (row["pnl"], row["trades"]), reverse=True)


def _monthly_performance(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty or "entry_time" not in frame:
        return []
    work = frame.dropna(subset=["entry_time"]).copy()
    if work.empty:
        return []
    entry_times = work["entry_time"].dt.tz_convert(None)
    work["month"] = entry_times.dt.to_period("M").astype(str)
    rows = []
    for month, group in work.groupby("month"):
        stats = _summary_stats(group)
        rows.append(
            {
                "month": str(month),
                "trades": stats["total_trades"],
                "pnl": stats["net_pnl"],
                "winrate": stats["winrate"],
                "avg_rr": stats["avg_rr"],
                "expectancy": stats["expectancy"],
            }
        )
    return sorted(rows, key=lambda row: row["month"])


def _cumulative_pnl(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    work = frame.sort_values("entry_time").copy()
    work["pnl"] = pd.to_numeric(work["pnl"], errors="coerce").fillna(0.0)
    work["cumulative_pnl"] = work["pnl"].cumsum()
    rows = []
    for row in work.itertuples(index=False):
        rows.append(
            {
                "time": _json_value(getattr(row, "entry_time", None)),
                "value": _finite_float(getattr(row, "cumulative_pnl", None)),
                "pnl": _finite_float(getattr(row, "pnl", None)),
                "rr": _finite_float(getattr(row, "rr", None)),
                "won": bool((getattr(row, "pnl", 0) or 0) > 0),
            }
        )
    return rows


def _equity_curve(equity: list[dict[str, Any]], run: dict[str, Any], frame: pd.DataFrame) -> list[dict[str, Any]]:
    if equity:
        rows = [
            {
                "time": row.get("time"),
                "balance": _finite_float(row.get("balance")),
                "equity": _finite_float(row.get("equity")),
                "drawdown_abs": _finite_float(row.get("drawdown_abs")),
                "drawdown_pct": _finite_float(row.get("drawdown_pct")),
                "open_positions": int(row.get("open_positions") or 0),
            }
            for row in equity
        ]
        return _downsample_points(rows)

    initial = float(run.get("initial_balance") or 0)
    output = []
    peak = initial
    for point in _cumulative_pnl(frame):
        equity_value = initial + float(point["value"] or 0)
        peak = max(peak, equity_value)
        drawdown_abs = equity_value - peak
        output.append(
            {
                "time": point["time"],
                "balance": equity_value,
                "equity": equity_value,
                "drawdown_abs": drawdown_abs,
                "drawdown_pct": drawdown_abs / peak if peak else None,
                "open_positions": 0,
            }
        )
    return _downsample_points(output)


def _downsample_points(rows: list[dict[str, Any]], max_points: int = 1500) -> list[dict[str, Any]]:
    if len(rows) <= max_points:
        return rows
    if max_points < 2:
        return rows[:max_points]
    indexes = {0, len(rows) - 1}
    step = (len(rows) - 1) / (max_points - 1)
    indexes.update(round(index * step) for index in range(max_points))
    return [rows[index] for index in sorted(indexes)]


def _rr_distribution(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty or "rr" not in frame:
        return []
    buckets = [
        ("<= -2R", -math.inf, -2),
        ("-2R to -1R", -2, -1),
        ("-1R to 0R", -1, 0),
        ("0R to 1R", 0, 1),
        ("1R to 2R", 1, 2),
        ("2R to 3R", 2, 3),
        ("> 3R", 3, math.inf),
    ]
    rr = pd.to_numeric(frame["rr"], errors="coerce")
    rows = []
    for label, low, high in buckets:
        mask = (rr >= low) & (rr < high)
        if high == math.inf:
            mask = rr >= low
        group = frame[mask.fillna(False)]
        rows.append(
            {
                "bucket": label,
                "trades": int(len(group)),
                "pnl": _summary_stats(group)["net_pnl"] if not group.empty else 0.0,
            }
        )
    return rows


def _comparisons(frame: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    if frame.empty:
        return {"symbols": [], "sources": []}
    return {
        "symbols": _breakdown(frame.assign(symbol=frame.get("symbol_code", "Unknown")).fillna({"symbol": "Unknown"}), "symbol")
        if "symbol_code" in frame
        else [],
        "sources": _breakdown(frame.assign(source=frame.get("source_name", "Unknown")).fillna({"source": "Unknown"}), "source")
        if "source_name" in frame
        else [],
    }


def _event_funnel(event_counts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    preferred = [
        "H1_SIGNAL",
        "BOS_FOUND",
        "MSS_FOUND",
        "AMD_PHASE_FOUND",
        "M15_DOUBLE_SWING_VALIDATED",
        "LEG_FOUND",
        "OTE_CREATED",
        "OB_SELECTED",
        "FVG_SELECTED",
        "PD_TOUCHED",
        "REJECTION_CONFIRMED",
        "TRADE_OPENED",
        "TRADE_CLOSED_TP",
        "TRADE_CLOSED_SL",
        "RISK_REJECTED",
    ]
    counts = {row["event_type"]: int(row["count"] or 0) for row in event_counts}
    ordered = [{"event_type": event_type, "count": counts.pop(event_type, 0)} for event_type in preferred]
    ordered.extend({"event_type": key, "count": value} for key, value in sorted(counts.items()))
    return ordered


def _diagnostics(summary: dict[str, Any], breakdowns: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    diagnostics = []
    total = int(summary["total_trades"])
    if total < 30:
        diagnostics.append(
            {
                "severity": "info",
                "title": "Sample encore leger",
                "detail": "Moins de 30 trades: les patterns de faiblesse sont utiles, mais pas encore statistiquement solides.",
            }
        )
    if summary["profit_factor"] is not None and summary["profit_factor"] < 1:
        diagnostics.append(
            {
                "severity": "critical",
                "title": "Profit factor sous 1",
                "detail": "La strategie rend plus sur les pertes qu'elle ne capte sur les gains: verifier filtrage, TP/SL et contexte de session.",
            }
        )
    if summary["winrate"] is not None and summary["winrate"] < 0.4:
        diagnostics.append(
            {
                "severity": "warning",
                "title": "Winrate bas",
                "detail": "Le taux de reussite est sous 40%. Avec un RR moyen insuffisant, c'est souvent le premier point de casse.",
            }
        )
    if int(summary["max_consecutive_losses"] or 0) >= 3:
        diagnostics.append(
            {
                "severity": "warning",
                "title": "Series de pertes",
                "detail": f"Max consecutive losses: {summary['max_consecutive_losses']}. A surveiller pour le dimensionnement du risque.",
            }
        )

    for group_name, label in (
        ("direction", "direction"),
        ("target_source", "target"),
        ("pd_type", "PD array"),
        ("session", "session"),
        ("hour_of_day", "heure"),
    ):
        for row in _worst_groups(breakdowns.get(group_name, []), total):
            diagnostics.append(
                {
                    "severity": "warning",
                    "title": f"Faiblesse par {label}: {row['name']}",
                    "detail": (
                        f"{row['trades']} trades, PnL {row['pnl']:.2f}, "
                        f"winrate {_format_pct(row['winrate'])}. Candidat prioritaire pour un filtre ou une analyse visuelle."
                    ),
                }
            )
    return diagnostics[:8]


def _worst_groups(rows: Iterable[dict[str, Any]], total_trades: int) -> list[dict[str, Any]]:
    min_trades = max(3, math.ceil(total_trades * 0.08))
    candidates = [row for row in rows if int(row["trades"]) >= min_trades and float(row["pnl"] or 0) < 0]
    return sorted(candidates, key=lambda row: float(row["pnl"] or 0))[:1]


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": run["run_id"],
        "status": run["status"],
        "run_type": run["run_type"],
        "start_time": run["start_time"],
        "end_time": run["end_time"],
        "created_at": run["created_at"],
        "symbol_code": run["symbol_code"],
        "source_name": run["source_name"],
        "strategy_name": run["strategy_name"],
        "strategy_version": run["strategy_version"],
        "parameter_set_name": run["parameter_set_name"],
        "timeframe": run["dataset_timeframe"],
    }


def _ratio(numerator: float, denominator: float) -> float | None:
    return float(numerator) / float(denominator) if denominator else None


def _profit_factor(gross_profit: float, gross_loss: float) -> float | None:
    if gross_loss == 0:
        return None
    return gross_profit / abs(gross_loss)


def _max_consecutive_losses(pnl: pd.Series) -> int:
    longest = 0
    current = 0
    for value in pnl:
        if value < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return int(longest)


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _format_pct(value: Any) -> str:
    number = _finite_float(value)
    if number is None:
        return "-"
    return f"{number * 100:.1f}%"
