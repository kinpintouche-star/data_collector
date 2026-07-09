from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Engine, bindparam, text
from sqlalchemy.orm import Session

from ict.api.analytics import build_analytics_payload
from ict.api.review import ReviewNotFoundError, _sanitize_mapping
from ict.archive.store import archive_configured, restore_from_r2
from ict.backtest.runner import run_dataset_backtest, run_dataset_strategy_definition_backtest
from ict.data.datasets import create_dataset_in_session
from ict.db.repositories import DatasetRepository, StrategyDefinitionRepository, json_safe
from ict.db.session import build_engine, session_scope
from ict.strategy.params import load_strategy_params


class BacktestAssetRequest(BaseModel):
    symbol_code: str
    source_name: str


class BacktestLaunchRequest(BaseModel):
    strategy_config: str = "configs/strategy_default.yaml"
    strategy_definition_id: str | None = None
    assets: list[BacktestAssetRequest] = Field(min_length=1)
    from_date: str = Field(alias="from")
    to_date: str = Field(alias="to")
    timeframe: str = "M1"
    label: str | None = None

    @field_validator("timeframe")
    @classmethod
    def uppercase_timeframe(cls, value: str) -> str:
        return value.upper()


class BacktestJobState(BaseModel):
    id: str
    status: Literal["queued", "running", "completed", "failed", "partial"]
    launch_id: str
    label: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    total_assets: int
    completed_assets: int = 0
    failed_assets: int = 0
    results: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


def list_backtest_options(engine: Engine | None = None) -> dict[str, Any]:
    engine = engine or build_engine()
    assets = _list_asset_options(engine)
    strategies = _list_strategy_configs()
    strategies.extend(_list_strategy_definitions(engine))
    default_to = _latest_complete_day(assets)
    default_from = (pd.Timestamp(default_to) - pd.Timedelta(days=30)).date().isoformat() if default_to else None
    return {
        "strategies": strategies,
        "assets": assets,
        "defaults": {
            "strategy_config": strategies[0]["path"] if strategies else "configs/strategy_default.yaml",
            "from": default_from,
            "to": default_to,
            "timeframe": "M1",
        },
    }


def create_backtest_job(payload: BacktestLaunchRequest) -> BacktestJobState:
    strategy_label = _strategy_label(payload)
    launch_id = str(uuid.uuid4())
    label = payload.label or _default_launch_label(strategy_label, payload)
    now = _now()
    state = {
        "id": str(uuid.uuid4()),
        "status": "queued",
        "launch_id": launch_id,
        "label": label,
        "created_at": now,
        "started_at": None,
        "finished_at": None,
        "total_assets": len(payload.assets),
        "completed_assets": 0,
        "failed_assets": 0,
        "results": [],
        "errors": [],
        "request": payload.model_dump(by_alias=True),
    }
    with _JOBS_LOCK:
        _JOBS[state["id"]] = state
    return BacktestJobState.model_validate(_public_job_state(state))


def get_backtest_job(job_id: str) -> BacktestJobState:
    with _JOBS_LOCK:
        state = _JOBS.get(job_id)
        if state is None:
            raise ReviewNotFoundError(f"Backtest job not found: {job_id}")
        return BacktestJobState.model_validate(_public_job_state(state))


def run_backtest_job(job_id: str) -> None:
    with _JOBS_LOCK:
        state = _JOBS[job_id]
        state["status"] = "running"
        state["started_at"] = _now()
        request = BacktestLaunchRequest.model_validate(state["request"])

    strategy_path = None
    params = None
    strategy_definition_id = request.strategy_definition_id
    if strategy_definition_id:
        strategy_label = f"builder:{strategy_definition_id}"
    else:
        strategy_path = _resolve_strategy_config(request.strategy_config)
        params = load_strategy_params(str(strategy_path))
        strategy_label = _relative_path(strategy_path)
    start = _parse_date(request.from_date)
    end = _parse_date(request.to_date)
    launch_metadata = {
        "launch_id": state["launch_id"],
        "launch_label": state["label"],
        "launched_from": "react_trading_lab",
        "strategy_config": strategy_label,
        "strategy_definition_id": strategy_definition_id,
        "asset_count": len(request.assets),
    }

    for asset in request.assets:
        try:
            archive_restore = _try_restore_archive_for_asset(asset, request.timeframe, start, end)
            with session_scope() as session:
                dataset_payload = create_dataset_in_session(
                    session,
                    asset.symbol_code,
                    asset.source_name,
                    request.timeframe,
                    start,
                    end,
                )
                dataset = DatasetRepository(session).require(dataset_payload["dataset_id"])
                if strategy_definition_id:
                    definition = StrategyDefinitionRepository(session).require(strategy_definition_id)
                    summary = run_dataset_strategy_definition_backtest(
                        session,
                        dataset,
                        strategy_definition_id,
                        f"builder_{definition.name}_{definition.version}",
                        run_metadata={
                            **launch_metadata,
                            "symbol_code": asset.symbol_code,
                            "source_name": asset.source_name,
                            "archive_restore": archive_restore,
                        },
                    )
                else:
                    assert params is not None
                    assert strategy_path is not None
                    summary = run_dataset_backtest(
                        session,
                        dataset,
                        params,
                        Path(strategy_path).stem,
                        run_metadata={
                            **launch_metadata,
                            "symbol_code": asset.symbol_code,
                            "source_name": asset.source_name,
                            "archive_restore": archive_restore,
                        },
                    )
                result = {
                    **json_safe(summary),
                    "symbol_code": asset.symbol_code,
                    "source_name": asset.source_name,
                    "status": "completed",
                }
            _mark_job_asset(job_id, result=result)
        except Exception as exc:  # noqa: BLE001 - surfaced to local UI for manual correction
            _mark_job_asset(
                job_id,
                error={
                    "symbol_code": asset.symbol_code,
                    "source_name": asset.source_name,
                    "status": "failed",
                    "error": str(exc),
                },
            )

    with _JOBS_LOCK:
        state = _JOBS[job_id]
        state["finished_at"] = _now()
        if state["failed_assets"] and state["completed_assets"]:
            state["status"] = "partial"
        elif state["failed_assets"]:
            state["status"] = "failed"
        else:
            state["status"] = "completed"


def fetch_run_groups(limit: int = 100, engine: Engine | None = None) -> list[dict[str, Any]]:
    query = text(
        """
        WITH grouped AS (
            SELECT
                COALESCE(r.metadata ->> 'launch_id', r.id::text) AS group_id,
                COALESCE(r.metadata ->> 'launch_label', s.symbol_code || ' / ' || ds.name || ' / ' || ps.name) AS label,
                r.id,
                r.status,
                r.start_time,
                r.end_time,
                r.created_at,
                s.symbol_code,
                ds.name AS source_name,
                sv.name AS strategy_name,
                sv.version AS strategy_version,
                ps.name AS parameter_set_name,
                COALESCE(rm.total_trades, 0) AS total_trades,
                COALESCE(rm.total_wins, 0) AS total_wins,
                COALESCE(rm.total_losses, 0) AS total_losses,
                COALESCE(rm.net_profit, 0) AS net_profit,
                rm.avg_rr,
                rm.profit_factor
            FROM backtest_runs r
            JOIN symbols s ON s.id = r.symbol_id
            JOIN data_sources ds ON ds.id = r.source_id
            JOIN strategy_versions sv ON sv.id = r.strategy_version_id
            JOIN parameter_sets ps ON ps.id = r.parameter_set_id
            LEFT JOIN run_metrics rm ON rm.run_id = r.id
        )
        SELECT
            group_id,
            MAX(label) AS label,
            MIN(start_time) AS start_time,
            MAX(end_time) AS end_time,
            MAX(created_at) AS created_at,
            COUNT(*)::integer AS run_count,
            ARRAY_AGG(id::text ORDER BY created_at DESC) AS run_ids,
            ARRAY_AGG(DISTINCT symbol_code ORDER BY symbol_code) AS symbols,
            ARRAY_AGG(DISTINCT source_name ORDER BY source_name) AS sources,
            MAX(strategy_name) AS strategy_name,
            MAX(strategy_version) AS strategy_version,
            MAX(parameter_set_name) AS parameter_set_name,
            SUM(total_trades)::integer AS total_trades,
            SUM(total_wins)::integer AS total_wins,
            SUM(total_losses)::integer AS total_losses,
            CASE WHEN SUM(total_trades) > 0 THEN SUM(total_wins)::float / SUM(total_trades) ELSE NULL END AS winrate,
            AVG(avg_rr) FILTER (WHERE avg_rr IS NOT NULL) AS avg_rr,
            SUM(net_profit) AS net_profit,
            AVG(profit_factor) FILTER (WHERE profit_factor IS NOT NULL) AS profit_factor,
            CASE
                WHEN COUNT(*) FILTER (WHERE status <> 'completed') > 0 THEN 'mixed'
                ELSE 'completed'
            END AS status
        FROM grouped
        GROUP BY group_id
        ORDER BY MAX(created_at) DESC
        LIMIT :limit
        """
    )
    with (engine or build_engine()).connect() as connection:
        rows = connection.execute(query, {"limit": int(limit)}).mappings().all()
    return [_sanitize_mapping(row) for row in rows]


def fetch_run_group_analytics(
    group_id: str,
    symbols: list[str] | None = None,
    engine: Engine | None = None,
) -> dict[str, Any]:
    engine = engine or build_engine()
    runs = _fetch_group_run_rows(group_id, engine)
    if not runs:
        raise ReviewNotFoundError(f"Run group not found: {group_id}")
    selected_symbols = {symbol.upper() for symbol in symbols or [] if symbol}
    active_runs = [run for run in runs if not selected_symbols or run["symbol_code"].upper() in selected_symbols]
    if not active_runs:
        raise ReviewNotFoundError(f"No runs match selected symbols for group: {group_id}")
    run_ids = [run["run_id"] for run in active_runs]
    trades = _fetch_group_trade_rows(run_ids, engine)
    events = _fetch_group_event_counts(run_ids, engine)
    pseudo_run = _group_public_run(group_id, active_runs, selected_symbols)
    payload = build_analytics_payload(pseudo_run, trades, [], events)
    payload["group"] = _group_summary(group_id, runs, active_runs)
    payload["available_symbols"] = sorted({run["symbol_code"] for run in runs})
    payload["selected_symbols"] = sorted({run["symbol_code"] for run in active_runs})
    return payload


def _list_asset_options(engine: Engine) -> list[dict[str, Any]]:
    query = text(
        """
        SELECT
            s.symbol_code,
            s.asset_type,
            ds.name AS source_name,
            ds.source_type,
            MIN(mc.time_open) AS start_time,
            MAX(mc.time_open) AS end_time,
            COUNT(*)::integer AS candles
        FROM market_candles mc
        JOIN symbols s ON s.id = mc.symbol_id
        JOIN data_sources ds ON ds.id = mc.source_id
        WHERE mc.timeframe = 'M1'
        GROUP BY s.symbol_code, s.asset_type, ds.name, ds.source_type
        HAVING COUNT(*) > 0
        ORDER BY s.symbol_code, ds.name
        """
    )
    with engine.connect() as connection:
        rows = connection.execute(query).mappings().all()
    return [_sanitize_mapping(row) for row in rows]


def _try_restore_archive_for_asset(
    asset: BacktestAssetRequest,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> dict[str, Any] | None:
    if timeframe.upper() != "M1" or not archive_configured():
        return None
    try:
        if _local_covers_window(asset.symbol_code, asset.source_name, timeframe, start, end):
            return {"status": "skipped", "reason": "local_already_covers_window"}
        result = restore_from_r2(
            since=start,
            until=end,
            symbols=[asset.symbol_code],
            source_names=[asset.source_name],
            timeframe=timeframe.upper(),
            continue_on_missing=True,
            skip_existing_local=True,
        )
        return result.as_dict()
    except Exception as exc:  # noqa: BLE001 - backtest gap handling remains the source of truth
        return {"status": "failed", "error": str(exc)}


def _local_covers_window(
    symbol_code: str,
    source_name: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> bool:
    query = text(
        """
        SELECT MIN(mc.time_open) AS first_time, MAX(mc.time_open) AS last_time
        FROM market_candles mc
        JOIN symbols s ON s.id = mc.symbol_id
        JOIN data_sources ds ON ds.id = mc.source_id
        WHERE s.symbol_code = :symbol_code
            AND ds.name = :source_name
            AND mc.timeframe = :timeframe
            AND mc.time_open >= :start
            AND mc.time_open <= :end
        """
    )
    with build_engine().connect() as connection:
        row = connection.execute(
            query,
            {
                "symbol_code": symbol_code,
                "source_name": source_name,
                "timeframe": timeframe.upper(),
                "start": start,
                "end": end,
            },
        ).mappings().one()
    first_time = row["first_time"]
    last_time = row["last_time"]
    return bool(first_time and last_time and first_time <= start and last_time >= end - pd.Timedelta(minutes=1))


def _list_strategy_configs() -> list[dict[str, str]]:
    strategies = []
    for path in sorted(Path("configs").glob("strategy*.yaml")):
        try:
            load_strategy_params(str(path))
        except Exception:
            continue
        strategies.append({"path": _relative_path(path), "label": path.stem.replace("_", " "), "kind": "yaml"})
    return strategies


def _list_strategy_definitions(engine: Engine) -> list[dict[str, str]]:
    try:
        with Session(engine) as session:
            rows = StrategyDefinitionRepository(session).list()
            return [
                {
                    "path": f"builder:{row.id}",
                    "label": f"{row.name} {row.version} ({row.status})",
                    "kind": "builder",
                    "status": row.status,
                    "strategy_definition_id": str(row.id),
                }
                for row in rows
            ]
    except Exception:
        return []


def _latest_complete_day(assets: list[dict[str, Any]]) -> str | None:
    dates = [pd.Timestamp(asset["end_time"]) for asset in assets if asset.get("end_time")]
    if not dates:
        return None
    return max(dates).date().isoformat()


def _resolve_strategy_config(value: str) -> Path:
    requested = Path(value)
    if not requested.is_absolute():
        requested = Path.cwd() / requested
    allowed = {Path.cwd() / item["path"] for item in _list_strategy_configs()}
    requested = requested.resolve()
    if requested not in {path.resolve() for path in allowed}:
        raise ValueError(f"Strategy config is not allowed: {value}")
    return requested


def _parse_date(value: str) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(timezone.utc)
    else:
        timestamp = timestamp.tz_convert(timezone.utc)
    return timestamp.to_pydatetime()


def _strategy_label(payload: BacktestLaunchRequest) -> str:
    if payload.strategy_definition_id:
        return f"builder:{payload.strategy_definition_id}"
    return _relative_path(_resolve_strategy_config(payload.strategy_config))


def _default_launch_label(strategy_label: str, payload: BacktestLaunchRequest) -> str:
    if len(payload.assets) == 1:
        asset = payload.assets[0]
        return f"{asset.symbol_code} / {asset.source_name} / {strategy_label}"
    return f"{len(payload.assets)} actifs / {strategy_label} / {payload.from_date} -> {payload.to_date}"


def _mark_job_asset(job_id: str, result: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> None:
    with _JOBS_LOCK:
        state = _JOBS[job_id]
        if result:
            state["results"].append(result)
            state["completed_assets"] += 1
        if error:
            state["errors"].append(error)
            state["failed_assets"] += 1


def _fetch_group_run_rows(group_id: str, engine: Engine) -> list[dict[str, Any]]:
    query = text(
        """
        SELECT
            r.id AS run_id,
            COALESCE(r.metadata ->> 'launch_id', r.id::text) AS group_id,
            COALESCE(r.metadata ->> 'launch_label', s.symbol_code || ' / ' || ds.name || ' / ' || ps.name) AS label,
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
            d.timeframe AS dataset_timeframe
        FROM backtest_runs r
        JOIN symbols s ON s.id = r.symbol_id
        JOIN data_sources ds ON ds.id = r.source_id
        JOIN strategy_versions sv ON sv.id = r.strategy_version_id
        JOIN parameter_sets ps ON ps.id = r.parameter_set_id
        JOIN datasets d ON d.id = r.dataset_id
        WHERE COALESCE(r.metadata ->> 'launch_id', r.id::text) = :group_id
        ORDER BY s.symbol_code, r.created_at DESC
        """
    )
    with engine.connect() as connection:
        rows = connection.execute(query, {"group_id": group_id}).mappings().all()
    return [_sanitize_mapping(row) for row in rows]


def _fetch_group_trade_rows(run_ids: list[str], engine: Engine) -> list[dict[str, Any]]:
    if not run_ids:
        return []
    query = text(
        """
        SELECT
            t.id,
            t.run_id,
            s.symbol_code,
            ds.name AS source_name,
            t.direction,
            t.entry_time,
            t.exit_time,
            t.exit_reason,
            t.pnl,
            t.pnl_points,
            t.rr,
            t.mae,
            t.mfe,
            t.pd_type,
            t.strategy_mode,
            t.session_name,
            t.metadata
        FROM trades t
        JOIN symbols s ON s.id = t.symbol_id
        JOIN data_sources ds ON ds.id = t.source_id
        WHERE t.run_id IN :run_ids
        ORDER BY t.entry_time
        """
    ).bindparams(bindparam("run_ids", expanding=True))
    with engine.connect() as connection:
        rows = connection.execute(query, {"run_ids": tuple(run_ids)}).mappings().all()
    return [_sanitize_mapping(row) for row in rows]


def _fetch_group_event_counts(run_ids: list[str], engine: Engine) -> list[dict[str, Any]]:
    if not run_ids:
        return []
    query = text(
        """
        SELECT event_type, COUNT(*)::integer AS count
        FROM setup_events
        WHERE run_id IN :run_ids
        GROUP BY event_type
        ORDER BY count DESC, event_type
        """
    ).bindparams(bindparam("run_ids", expanding=True))
    with engine.connect() as connection:
        rows = connection.execute(query, {"run_ids": tuple(run_ids)}).mappings().all()
    return [_sanitize_mapping(row) for row in rows]


def _group_public_run(group_id: str, runs: list[dict[str, Any]], selected_symbols: set[str]) -> dict[str, Any]:
    first = runs[0]
    symbols = sorted({run["symbol_code"] for run in runs})
    sources = sorted({run["source_name"] for run in runs})
    return {
        "run_id": group_id,
        "status": "completed" if all(run["status"] == "completed" for run in runs) else "mixed",
        "run_type": "backtest_group" if len(runs) > 1 else first["run_type"],
        "start_time": min(run["start_time"] for run in runs),
        "end_time": max(run["end_time"] for run in runs),
        "created_at": max(run["created_at"] for run in runs),
        "initial_balance": sum(float(run.get("initial_balance") or 0) for run in runs),
        "final_balance": sum(float(run.get("final_balance") or 0) for run in runs),
        "symbol_code": ", ".join(sorted(selected_symbols)) if selected_symbols else ", ".join(symbols),
        "source_name": ", ".join(sources),
        "strategy_name": first["strategy_name"],
        "strategy_version": first["strategy_version"],
        "parameter_set_name": first["parameter_set_name"],
        "dataset_timeframe": first["dataset_timeframe"],
    }


def _group_summary(group_id: str, all_runs: list[dict[str, Any]], active_runs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": group_id,
        "label": all_runs[0]["label"],
        "run_count": len(all_runs),
        "active_run_count": len(active_runs),
        "run_ids": [str(run["run_id"]) for run in active_runs],
        "symbols": sorted({run["symbol_code"] for run in all_runs}),
        "selected_symbols": sorted({run["symbol_code"] for run in active_runs}),
    }


def _public_job_state(state: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in state.items() if key != "request"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.as_posix()
