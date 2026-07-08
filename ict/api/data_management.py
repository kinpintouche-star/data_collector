from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml
from pydantic import BaseModel, Field
from sqlalchemy import Engine, text

from ict.api.review import ReviewNotFoundError, _json_value, _sanitize_mapping
from ict.archive.store import archive_configured, archive_status, restore_from_r2
from ict.core.config import get_settings
from ict.data.ingest import ingest_market_data
from ict.db.session import build_engine
from ict.live.sync import sync_remote_candles


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_UNIVERSE_PATH = PROJECT_ROOT / "configs" / "universe_default_40.yaml"

DataFetchChannel = Literal["auto", "r2", "neon", "databento"]


class DataFetchAsset(BaseModel):
    symbol_code: str
    source_name: str


class DataFetchJobRequest(BaseModel):
    channel: DataFetchChannel = "auto"
    assets: list[DataFetchAsset] = Field(min_length=1)
    fallback_days: int = Field(default=180, ge=1, le=3650)
    overlap_minutes: int = Field(default=5, ge=0, le=240)
    neon_limit: int = Field(default=250000, ge=1000, le=2_000_000)
    max_databento_usd: float = Field(default=5.0, gt=0, le=125.0)


class DataFetchJobState(BaseModel):
    id: str
    status: Literal["queued", "running", "completed", "failed", "partial"]
    channel: DataFetchChannel
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    total_assets: int
    completed_assets: int = 0
    skipped_assets: int = 0
    failed_assets: int = 0
    results: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


_DATA_JOBS: dict[str, dict[str, Any]] = {}
_DATA_JOBS_LOCK = threading.Lock()


def fetch_data_coverage(engine: Engine | None = None, now: datetime | None = None) -> dict[str, Any]:
    engine = engine or build_engine()
    rows = build_data_coverage_rows(engine, now=now)
    summary = _coverage_summary(rows)
    return {
        "generated_at": _now(),
        "settings": _data_settings_payload(),
        "summary": summary,
        "rows": rows,
    }


def fetch_data_api_usage(engine: Engine | None = None) -> dict[str, Any]:
    engine = engine or build_engine()
    return {
        "generated_at": _now(),
        "settings": _data_settings_payload(),
        "rows": _api_usage_rows(build_data_usage_rows(engine)),
    }


def create_data_fetch_job(payload: DataFetchJobRequest) -> DataFetchJobState:
    now = _now()
    state = {
        "id": str(uuid.uuid4()),
        "status": "queued",
        "channel": payload.channel,
        "created_at": now,
        "started_at": None,
        "finished_at": None,
        "total_assets": len(payload.assets),
        "completed_assets": 0,
        "skipped_assets": 0,
        "failed_assets": 0,
        "results": [],
        "errors": [],
        "request": payload.model_dump(),
    }
    with _DATA_JOBS_LOCK:
        _DATA_JOBS[state["id"]] = state
    return DataFetchJobState.model_validate(_public_job_state(state))


def get_data_fetch_job(job_id: str) -> DataFetchJobState:
    with _DATA_JOBS_LOCK:
        state = _DATA_JOBS.get(job_id)
        if state is None:
            raise ReviewNotFoundError(f"Data fetch job not found: {job_id}")
        return DataFetchJobState.model_validate(_public_job_state(state))


def run_data_fetch_job(job_id: str) -> None:
    with _DATA_JOBS_LOCK:
        state = _DATA_JOBS[job_id]
        state["status"] = "running"
        state["started_at"] = _now()
        request = DataFetchJobRequest.model_validate(state["request"])

    try:
        coverage_rows = build_data_coverage_rows()
        rows_by_key = {(row["symbol_code"], row["source_name"]): row for row in coverage_rows}
        for asset in request.assets:
            row = rows_by_key.get((asset.symbol_code, asset.source_name))
            if row is None:
                _mark_data_job_asset(
                    job_id,
                    error={
                        "symbol_code": asset.symbol_code,
                        "source_name": asset.source_name,
                        "status": "failed",
                        "error": "Asset/source is not configured in the data universe.",
                    },
                )
                continue
            try:
                result = fetch_missing_for_row(row, request)
                _mark_data_job_asset(job_id, result=result)
            except Exception as exc:  # noqa: BLE001 - surfaced in the local UI per asset
                _mark_data_job_asset(
                    job_id,
                    error={
                        "symbol_code": asset.symbol_code,
                        "source_name": asset.source_name,
                        "channel": resolve_channel(row, request.channel),
                        "status": "failed",
                        "error": str(exc),
                    },
                )
    finally:
        with _DATA_JOBS_LOCK:
            state = _DATA_JOBS[job_id]
            state["finished_at"] = _now()
            if state["failed_assets"] and (state["completed_assets"] or state["skipped_assets"]):
                state["status"] = "partial"
            elif state["failed_assets"]:
                state["status"] = "failed"
            else:
                state["status"] = "completed"


def build_data_coverage_rows(engine: Engine | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
    engine = engine or build_engine()
    targets = _load_universe_targets()
    if targets.empty:
        return []
    coverage = _read_local_coverage_frame(engine, targets)
    sources = _read_optional_frame(engine, "SELECT name AS source_name, source_type, config FROM data_sources ORDER BY name")
    live = _read_optional_frame(engine, "SELECT * FROM mart_live_collector ORDER BY symbol_code, source_name")
    r2_status = _r2_status_for_targets(targets)

    base = targets.rename(columns={"source_name": "local_source"}).copy()
    if not coverage.empty:
        coverage = coverage.copy()
        coverage["timeframe"] = coverage["timeframe"].astype(str).str.upper()
        coverage = coverage[coverage["timeframe"] == "M1"]
        wanted = [
            "symbol_code",
            "source_name",
            "asset_type",
            "timeframe",
            "candle_rows",
            "first_candle_time",
            "last_candle_time",
            "last_ingested_at",
            "flagged_candles",
            "sample_source_symbol",
        ]
        base = base.merge(
            coverage[[column for column in wanted if column in coverage.columns]].rename(columns={"source_name": "local_source"}),
            on=["symbol_code", "local_source"],
            how="left",
        )
    if not sources.empty:
        base = base.merge(sources.rename(columns={"source_name": "local_source"}), on="local_source", how="left")
    if not live.empty:
        live = live.copy()
        live["neon_last_candle_time"] = pd.to_datetime(live.get("last_candle_time"), errors="coerce", utc=True)
        live_rollup = (
            live.groupby("symbol_code", as_index=False)
            .agg(
                neon_last_candle_time=("neon_last_candle_time", "max"),
                neon_enabled=("enabled", "max"),
                neon_status=("status", lambda values: ", ".join(sorted({str(value) for value in values if pd.notna(value)}))),
                neon_sources=("source_name", lambda values: ", ".join(sorted({str(value) for value in values if pd.notna(value)}))),
            )
        )
        base = base.merge(live_rollup, on="symbol_code", how="left")

    rows = []
    for row in base.to_dict(orient="records"):
        row.update(r2_status.get((str(row.get("symbol_code")).upper(), str(row.get("local_source"))), {}))
        rows.append(_coverage_row_payload(row, now=now))
    return rows


def build_data_usage_rows(engine: Engine | None = None) -> list[dict[str, Any]]:
    engine = engine or build_engine()
    targets = _load_universe_targets()
    if targets.empty:
        return []
    sources = _read_optional_frame(engine, "SELECT name AS source_name, source_type FROM data_sources ORDER BY name")
    base = targets.rename(columns={"source_name": "local_source"}).copy()
    if not sources.empty:
        base = base.merge(sources.rename(columns={"source_name": "local_source"}), on="local_source", how="left")
    rows = []
    for row in base.to_dict(orient="records"):
        rows.append(
            {
                "symbol_code": row.get("symbol_code"),
                "source_name": row.get("local_source"),
                "recommended_channel": resolve_channel({"source_type": row.get("source_type")}, "auto"),
            }
        )
    return rows


def _read_local_coverage_frame(engine: Engine, targets: pd.DataFrame) -> pd.DataFrame:
    pairs = targets[["symbol_code", "source_name"]].drop_duplicates().reset_index(drop=True)
    if pairs.empty:
        return pd.DataFrame()
    params: dict[str, str] = {}
    values = []
    for index, row in pairs.iterrows():
        symbol_key = f"symbol_{index}"
        source_key = f"source_{index}"
        params[symbol_key] = str(row["symbol_code"])
        params[source_key] = str(row["source_name"])
        values.append(f"(:{symbol_key}, :{source_key})")

    query = f"""
        WITH target(symbol_code, source_name) AS (
            VALUES {", ".join(values)}
        ),
        target_ids AS (
            SELECT
                target.symbol_code,
                symbols.asset_type,
                target.source_name,
                symbols.id AS symbol_id,
                data_sources.id AS source_id
            FROM target
            LEFT JOIN symbols ON symbols.symbol_code = target.symbol_code
            LEFT JOIN data_sources ON data_sources.name = target.source_name
        )
        SELECT
            target_ids.symbol_code,
            target_ids.asset_type,
            target_ids.source_name,
            'M1' AS timeframe,
            COALESCE(stats.candle_rows, 0) AS candle_rows,
            first_candle.time_open AS first_candle_time,
            last_candle.time_open AS last_candle_time,
            last_candle.ingested_at AS last_ingested_at,
            1 AS source_symbol_count,
            last_candle.source_symbol AS sample_source_symbol,
            0 AS flagged_candles,
            NULL::numeric AS avg_spread
        FROM target_ids
        LEFT JOIN LATERAL (
            SELECT candle.time_open
            FROM market_candles candle
            WHERE candle.symbol_id = target_ids.symbol_id
                AND candle.source_id = target_ids.source_id
                AND candle.timeframe = 'M1'
            ORDER BY candle.time_open ASC
            LIMIT 1
        ) first_candle ON TRUE
        LEFT JOIN LATERAL (
            SELECT candle.time_open, candle.ingested_at, candle.source_symbol
            FROM market_candles candle
            WHERE candle.symbol_id = target_ids.symbol_id
                AND candle.source_id = target_ids.source_id
                AND candle.timeframe = 'M1'
            ORDER BY candle.time_open DESC
            LIMIT 1
        ) last_candle ON TRUE
        LEFT JOIN LATERAL (
            SELECT
                COUNT(*)::bigint AS candle_rows
            FROM market_candles candle
            WHERE candle.symbol_id = target_ids.symbol_id
                AND candle.source_id = target_ids.source_id
                AND candle.timeframe = 'M1'
        ) stats ON TRUE
        ORDER BY target_ids.symbol_code, target_ids.source_name
    """
    return _read_optional_frame(engine, query, params)


def resolve_channel(row: dict[str, Any], requested: DataFetchChannel) -> str:
    if requested != "auto":
        return "R2" if requested == "r2" else requested.capitalize()
    source_type = str(row.get("source_type") or "").lower()
    if source_type == "databento":
        return "Databento"
    return "R2"


def fetch_missing_for_row(row: dict[str, Any], request: DataFetchJobRequest, now: datetime | None = None) -> dict[str, Any]:
    channel = resolve_channel(row, request.channel)
    if not _channel_is_applicable(row, channel):
        return _skipped(row, channel, "channel_not_applicable")

    if channel == "Databento":
        if bool(row.get("complete_day_ok")):
            return _skipped(row, channel, "already_complete_day")
        result = ingest_market_data(
            str(row["symbol_code"]),
            str(row["source_name"]),
            "M1",
            missing_start(row, request.fallback_days, request.overlap_minutes, now),
            now or datetime.now(timezone.utc),
            max_cost_usd=request.max_databento_usd,
        )
        return {
            "symbol_code": row["symbol_code"],
            "source_name": row["source_name"],
            "channel": channel,
            "status": "completed",
            "rows_fetched": result["rows_fetched"],
            "rows_inserted": result["rows_inserted"],
            "rows_updated": result["rows_updated"],
            "rows_skipped": result["rows_skipped"],
            "rows_written": result["rows_written"],
        }

    if channel == "R2":
        if not archive_configured():
            raise RuntimeError("R2 archive is not configured.")
        end = latest_complete_utc_day(now)
        result = restore_from_r2(
            since=missing_start(row, request.fallback_days, request.overlap_minutes, now),
            until=end,
            symbols=[str(row["symbol_code"])],
            source_names=[str(row["source_name"])],
            timeframe="M1",
            continue_on_missing=True,
        )
        if result.rows_read == 0:
            return {
                **_skipped(row, channel, "no_r2_data"),
                "rows_read": 0,
                "until": _iso(end),
                "missing_partitions": len(result.missing),
            }
        return {
            "symbol_code": row["symbol_code"],
            "source_name": row["source_name"],
            "channel": channel,
            "status": "completed",
            "rows_read": result.rows_read,
            "rows_inserted": result.rows_inserted,
            "rows_updated": result.rows_updated,
            "rows_written": result.rows_written,
            "partitions": len(result.partitions),
            "missing_partitions": len(result.missing),
        }

    remote_url = get_settings().live_remote_database_url
    if not remote_url:
        raise RuntimeError("LIVE_REMOTE_DATABASE_URL is not configured.")
    local_last = _parse_time(row.get("local_last"))
    neon_last = _parse_time(row.get("neon_last"))
    if local_last is not None and neon_last is not None and neon_last <= local_last:
        return _skipped(row, channel, "local_already_matches_neon")
    until = neon_last or now or datetime.now(timezone.utc)
    result = sync_remote_candles(
        remote_url,
        since=missing_start(row, request.fallback_days, request.overlap_minutes, now),
        until=until,
        symbols=[str(row["symbol_code"])],
        limit=request.neon_limit,
    )
    if result.rows_read == 0:
        return {
            **_skipped(row, channel, "no_neon_data"),
            "rows_read": 0,
            "until": _iso(until),
        }
    return {
        "symbol_code": row["symbol_code"],
        "source_name": row["source_name"],
        "channel": channel,
        "status": "completed",
        "rows_read": result.rows_read,
        "rows_inserted": result.rows_inserted,
        "rows_updated": result.rows_updated,
        "rows_written": result.rows_written,
    }


def missing_start(row: dict[str, Any], fallback_days: int, overlap_minutes: int, now: datetime | None = None) -> datetime:
    local_last = _parse_time(row.get("local_last"))
    if local_last is not None:
        return local_last - timedelta(minutes=overlap_minutes)
    return (now or datetime.now(timezone.utc)) - timedelta(days=fallback_days)


def latest_complete_utc_day(now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


def _data_settings_payload() -> dict[str, bool]:
    settings = get_settings()
    return {
        "r2_configured": archive_configured(),
        "neon_configured": bool(settings.live_remote_database_url),
        "databento_configured": bool(settings.databento_api_key),
    }


def _coverage_row_payload(row: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    current_midnight = latest_complete_utc_day(now)
    complete_day_threshold = current_midnight - timedelta(minutes=1)
    local_last = _parse_time(row.get("last_candle_time"))
    neon_last = _parse_time(row.get("neon_last_candle_time"))
    rows = int(row.get("candle_rows") or 0)
    complete_day_ok = bool(local_last and local_last >= complete_day_threshold)
    today_present = bool(local_last and local_last >= current_midnight)
    missing_from_neon = None
    if local_last and neon_last:
        missing_from_neon = max(0.0, (neon_last - local_last).total_seconds() / 60)
    status = "empty" if rows == 0 else "complete_day_ok" if complete_day_ok else "stale"
    return {
        "symbol_code": row.get("symbol_code"),
        "group": row.get("group"),
        "source_name": row.get("local_source"),
        "source_type": row.get("source_type"),
        "asset_type": row.get("asset_type"),
        "recommended_channel": resolve_channel({"source_type": row.get("source_type")}, "auto"),
        "candle_rows": rows,
        "first_candle_time": _iso(row.get("first_candle_time")),
        "last_candle_time": _iso(local_last),
        "last_ingested_at": _iso(row.get("last_ingested_at")),
        "local_last": _iso(local_last),
        "neon_last": _iso(neon_last),
        "r2_available": bool(row.get("r2_available") or False),
        "r2_last": row.get("r2_last"),
        "r2_partitions": int(row.get("r2_partitions") or 0),
        "r2_rows": int(row.get("r2_rows") or 0),
        "r2_encrypted_bytes": int(row.get("r2_encrypted_bytes") or 0),
        "neon_enabled": bool(row.get("neon_enabled") or False),
        "neon_status": row.get("neon_status"),
        "neon_sources": row.get("neon_sources"),
        "missing_from_neon_min": missing_from_neon,
        "flagged_candles": int(row.get("flagged_candles") or 0),
        "sample_source_symbol": row.get("sample_source_symbol"),
        "complete_day_ok": complete_day_ok,
        "today_present": today_present,
        "freshness_status": status,
        "needs_attention": status != "complete_day_ok" or bool(missing_from_neon and missing_from_neon > 0),
    }


def _load_universe_targets(path: Path = DEFAULT_UNIVERSE_PATH) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["symbol_code", "source_name", "group"])
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = []
    for asset in payload.get("assets", []):
        source_value = asset.get("sources", asset.get("source"))
        sources = [source_value] if isinstance(source_value, str) else list(source_value or [])
        for source in sources:
            rows.append({"symbol_code": asset["symbol"], "source_name": str(source), "group": asset.get("group")})
    return pd.DataFrame.from_records(rows)


def _read_optional_frame(engine: Engine, query: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
    try:
        return pd.read_sql(text(query), engine, params=params)
    except Exception:
        return pd.DataFrame()


def _coverage_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "assets": len({row["symbol_code"] for row in rows}),
        "asset_sources": len(rows),
        "complete_day_ok": sum(1 for row in rows if row["complete_day_ok"]),
        "today_present": sum(1 for row in rows if row["today_present"]),
        "empty": sum(1 for row in rows if row["freshness_status"] == "empty"),
        "stale": sum(1 for row in rows if row["freshness_status"] == "stale"),
        "total_candles": sum(int(row["candle_rows"] or 0) for row in rows),
        "flagged_candles": sum(int(row["flagged_candles"] or 0) for row in rows),
    }


def _api_usage_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    info = {
        "R2": {
            "usage": "Archive gratuite cible: GitHub Actions stocke les candles canonisees en Parquet/ZSTD chiffre.",
            "limits": "Borne par le free tier R2, les limites GitHub Actions, et les garde-fous max upload/download.",
            "current_split": "Restore par partitions journalieres utiles, puis backtest depuis la base locale.",
            "cost": "Free tier target; aucune source payante schedulee.",
        },
        "Neon": {
            "usage": "Fallback/transition SQL recent, plus source principale.",
            "limits": "Bound by Neon free storage/compute and row limit.",
            "current_split": "Pull missing recent candles if R2 is not available yet.",
            "cost": "Free tier target.",
        },
        "Databento": {
            "usage": "Manual native paid market data for assets not covered cleanly for free, especially MNQ.",
            "limits": "Every request uses a max USD guard before download.",
            "current_split": "Never used by the scheduled collector. Runs only when triggered from the app.",
            "cost": "Metered; MNQ six-month M1 reference was about $0.64.",
        },
    }
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        channel = str(row["recommended_channel"])
        entry = grouped.setdefault(channel, {"fetch_channel": channel, "asset_count": 0, "assets": set(), "sources": set()})
        entry["assets"].add(str(row["symbol_code"]))
        entry["sources"].add(str(row["source_name"]))
    output = []
    for channel, entry in sorted(grouped.items()):
        meta = info.get(channel, {})
        output.append(
            {
                "fetch_channel": channel,
                "asset_count": len(entry["assets"]),
                "assets": ", ".join(sorted(entry["assets"])),
                "sources": ", ".join(sorted(entry["sources"])),
                **meta,
            }
        )
    return output


def _channel_is_applicable(row: dict[str, Any], channel: str) -> bool:
    source_type = str(row.get("source_type") or "").lower()
    if channel == "Databento":
        return source_type == "databento"
    if channel == "Neon":
        return source_type != "databento"
    if channel == "R2":
        return source_type != "databento"
    return False


def _r2_status_for_targets(targets: pd.DataFrame) -> dict[tuple[str, str], dict[str, Any]]:
    if targets.empty or not archive_configured():
        return {}
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for row in targets.to_dict(orient="records"):
        symbol_code = str(row["symbol_code"]).upper()
        source_name = str(row["source_name"])
        try:
            status = archive_status(symbols=[symbol_code], source_names=[source_name], timeframe="M1")
            output.update(status)
        except Exception:
            output[(symbol_code, source_name)] = {"r2_available": False, "r2_error": "status_unavailable"}
    return output


def _mark_data_job_asset(job_id: str, result: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> None:
    with _DATA_JOBS_LOCK:
        state = _DATA_JOBS[job_id]
        if error is not None:
            state["failed_assets"] += 1
            state["errors"].append(error)
        elif result is not None and result.get("status") == "skipped":
            state["skipped_assets"] += 1
            state["results"].append(result)
        elif result is not None:
            state["completed_assets"] += 1
            state["results"].append(result)


def _public_job_state(state: dict[str, Any]) -> dict[str, Any]:
    return {key: _json_value(value) for key, value in state.items() if key != "request"}


def _skipped(row: dict[str, Any], channel: str, reason: str) -> dict[str, Any]:
    return {
        "symbol_code": row["symbol_code"],
        "source_name": row["source_name"],
        "channel": channel,
        "status": "skipped",
        "reason": reason,
    }


def _parse_time(value: Any) -> datetime | None:
    if value is None or pd.isna(value):
        return None
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.to_pydatetime()


def _iso(value: Any) -> str | None:
    parsed = _parse_time(value)
    return parsed.isoformat() if parsed else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
