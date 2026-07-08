from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from uuid import UUID

import pandas as pd
from sqlalchemy import text

from ict.db.session import build_engine
from ict.live.config import LiveSource, load_live_sources
from ict.live.providers import fetch_live_source, previous_utc_day_window
from ict.live.sync import LiveSyncResult, refresh_remote_source_state, upsert_frame_to_remote_compact


@dataclass(frozen=True)
class AssetCollectionResult:
    symbol_code: str
    source_name: str
    source_symbol: str
    provider: str
    timeframe: str
    status: str
    rows_fetched: int
    rows_written: int
    rows_inserted: int
    rows_updated: int
    last_candle_time: datetime | None
    error_message: str | None = None

    def as_dict(self) -> dict:
        return {
            "symbol_code": self.symbol_code,
            "source_name": self.source_name,
            "source_symbol": self.source_symbol,
            "provider": self.provider,
            "timeframe": self.timeframe,
            "status": self.status,
            "rows_fetched": self.rows_fetched,
            "rows_written": self.rows_written,
            "rows_inserted": self.rows_inserted,
            "rows_updated": self.rows_updated,
            "last_candle_time": self.last_candle_time,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class RemoteCollectionSummary:
    run_id: str
    status: str
    assets_requested: int
    assets_succeeded: int
    assets_failed: int
    rows_fetched: int
    rows_written: int
    rows_inserted: int
    rows_updated: int
    since: datetime
    until: datetime
    results: list[AssetCollectionResult]

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "assets_requested": self.assets_requested,
            "assets_succeeded": self.assets_succeeded,
            "assets_failed": self.assets_failed,
            "rows_fetched": self.rows_fetched,
            "rows_written": self.rows_written,
            "rows_inserted": self.rows_inserted,
            "rows_updated": self.rows_updated,
            "since": self.since,
            "until": self.until,
            "results": [result.as_dict() for result in self.results],
        }


FetchSource = Callable[[LiveSource, datetime, datetime], pd.DataFrame]
UpsertFrame = Callable[[pd.DataFrame, str], LiveSyncResult]


def collect_remote_live(
    remote_database_url: str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    symbols: Iterable[str] | None = None,
    config: str = "configs/live_sources.yaml",
    max_priority: int | None = None,
    max_workers: int = 4,
    upload_chunk_rows: int = 2500,
    trigger_type: str = "github_actions",
    submit_pause_seconds: float = 0.25,
    dry_run: bool = False,
    log_path: str | Path | None = None,
    fetch_source: FetchSource = fetch_live_source,
    upsert_frame: UpsertFrame = upsert_frame_to_remote_compact,
) -> RemoteCollectionSummary:
    window_since, window_until = _collection_window(since, until)
    sources = _selected_sources(config, symbols, max_priority=max_priority)
    logger = JsonlEventLogger(log_path)
    logger.emit(
        "run_started",
        trigger_type=trigger_type,
        dry_run=dry_run,
        since=window_since.isoformat(),
        until=window_until.isoformat(),
        assets_requested=len(sources),
        symbols=[source.symbol_code for source in sources],
        max_priority=max_priority,
    )
    if dry_run:
        results = [dry_run_result(source) for source in sources]
        summary = summarize_remote_collection("dry-run", "dry_run", window_since, window_until, results)
        logger.emit("run_completed", **_summary_log_payload(summary))
        return summary

    run_id = create_remote_collector_run(
        remote_database_url,
        trigger_type,
        len(sources),
        window_since,
        window_until,
        metadata=_run_metadata(trigger_type, window_since, window_until, symbols, max_priority),
    )
    results: list[AssetCollectionResult] = []
    max_workers = max(1, min(max_workers, max(1, len(sources))))

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for source in sources:
                logger.emit("asset_submitted", **_source_log_payload(source))
                futures[executor.submit(fetch_source, source, window_since, window_until)] = source
                if submit_pause_seconds > 0:
                    time.sleep(submit_pause_seconds)

            for future in as_completed(futures):
                source = futures[future]
                try:
                    frame = future.result()
                    result = upload_asset_frame(
                        remote_database_url,
                        source,
                        frame,
                        upload_chunk_rows,
                        upsert_frame=upsert_frame,
                    )
                    if result.status == "ok":
                        refresh_remote_source_state(remote_database_url, symbols=[source.symbol_code], config=config)
                        resolve_remote_incident(remote_database_url, source)
                    else:
                        record_remote_source_failure(
                            remote_database_url,
                            source,
                            result.error_message or "No closed candles returned for the requested window.",
                        )
                except Exception as exc:
                    result = failed_result(source, exc)
                    record_remote_source_failure(remote_database_url, source, result.error_message or str(exc))
                results.append(result)
                logger.emit(
                    "asset_completed" if result.status == "ok" else "asset_failed",
                    **result.as_dict(),
                )
    except Exception as exc:
        finish_remote_collector_run(remote_database_url, run_id, "failed", results, str(exc))
        logger.emit("run_failed", error_message=_short_error(exc), completed_assets=len(results))
        raise

    status = "completed"
    finish_remote_collector_run(remote_database_url, run_id, status, results)
    summary = summarize_remote_collection(run_id, status, window_since, window_until, results)
    logger.emit("run_completed", **_summary_log_payload(summary))
    return summary


def upload_asset_frame(
    remote_database_url: str,
    source: LiveSource,
    frame: pd.DataFrame,
    upload_chunk_rows: int,
    *,
    upsert_frame: UpsertFrame = upsert_frame_to_remote_compact,
) -> AssetCollectionResult:
    if frame.empty:
        return AssetCollectionResult(
            symbol_code=source.symbol_code,
            source_name=source.source_name,
            source_symbol=source.source_symbol,
            provider=source.provider,
            timeframe=source.timeframe,
            status="stale",
            rows_fetched=0,
            rows_written=0,
            rows_inserted=0,
            rows_updated=0,
            last_candle_time=None,
            error_message="No closed candles returned for the requested window.",
        )

    aggregate = LiveSyncResult(0, 0, 0, 0, [])
    for chunk in _frame_chunks(frame, upload_chunk_rows):
        partial = upsert_frame(chunk, remote_database_url)
        aggregate = _merge_sync_results([aggregate, partial])

    last_candle = pd.Timestamp(frame["time_open"].max()).to_pydatetime()
    return AssetCollectionResult(
        symbol_code=source.symbol_code,
        source_name=source.source_name,
        source_symbol=source.source_symbol,
        provider=source.provider,
        timeframe=source.timeframe,
        status="ok",
        rows_fetched=int(len(frame)),
        rows_written=aggregate.rows_written,
        rows_inserted=aggregate.rows_inserted,
        rows_updated=aggregate.rows_updated,
        last_candle_time=last_candle,
    )


def failed_result(source: LiveSource, exc: Exception) -> AssetCollectionResult:
    return AssetCollectionResult(
        symbol_code=source.symbol_code,
        source_name=source.source_name,
        source_symbol=source.source_symbol,
        provider=source.provider,
        timeframe=source.timeframe,
        status="error",
        rows_fetched=0,
        rows_written=0,
        rows_inserted=0,
        rows_updated=0,
        last_candle_time=None,
        error_message=_short_error(exc),
    )


def dry_run_result(source: LiveSource) -> AssetCollectionResult:
    return AssetCollectionResult(
        symbol_code=source.symbol_code,
        source_name=source.source_name,
        source_symbol=source.source_symbol,
        provider=source.provider,
        timeframe=source.timeframe,
        status="dry_run",
        rows_fetched=0,
        rows_written=0,
        rows_inserted=0,
        rows_updated=0,
        last_candle_time=None,
    )


def create_remote_collector_run(
    remote_database_url: str,
    trigger_type: str,
    assets_requested: int,
    since: datetime,
    until: datetime,
    metadata: dict | None = None,
) -> str:
    payload = {
        "runtime": "github-actions-python",
        "since": since.isoformat(),
        "until": until.isoformat(),
        **(metadata or {}),
    }
    with build_engine(remote_database_url).begin() as connection:
        return str(
            connection.execute(
                text(
                    """
                    INSERT INTO collector_runs (trigger_type, status, started_at, assets_requested, metadata)
                    VALUES (:trigger_type, 'running', now(), :assets_requested, CAST(:metadata AS jsonb))
                    RETURNING id
                    """
                ),
                {
                    "trigger_type": trigger_type,
                    "assets_requested": assets_requested,
                    "metadata": json.dumps(payload),
                },
            ).scalar_one()
        )


def finish_remote_collector_run(
    remote_database_url: str,
    run_id: str,
    status: str,
    results: list[AssetCollectionResult],
    error_message: str | None = None,
) -> None:
    with build_engine(remote_database_url).begin() as connection:
        connection.execute(
            text(
                """
                UPDATE collector_runs
                SET
                    status = :status,
                    finished_at = now(),
                    duration_ms = EXTRACT(EPOCH FROM (now() - started_at))::int * 1000,
                    assets_succeeded = :assets_succeeded,
                    assets_failed = :assets_failed,
                    rows_fetched = :rows_fetched,
                    rows_written = :rows_written,
                    error_message = :error_message
                WHERE id = :run_id
                """
            ),
            {
                "run_id": UUID(str(run_id)),
                "status": status,
                "assets_succeeded": sum(1 for result in results if result.status == "ok"),
                "assets_failed": sum(1 for result in results if result.status == "error"),
                "rows_fetched": sum(result.rows_fetched for result in results),
                "rows_written": sum(result.rows_written for result in results),
                "error_message": error_message,
            },
        )


def record_remote_source_failure(remote_database_url: str, source: LiveSource, message: str) -> None:
    with build_engine(remote_database_url).begin() as connection:
        row = connection.execute(
            text(
                """
                UPDATE collector_source_state css
                SET
                    status = 'error',
                    last_error_at = now(),
                    last_error_message = :message,
                    consecutive_failures = css.consecutive_failures + 1,
                    updated_at = now()
                FROM symbols s, data_sources ds
                WHERE css.symbol_id = s.id
                    AND css.source_id = ds.id
                    AND s.symbol_code = :symbol_code
                    AND ds.name = :source_name
                    AND css.timeframe = :timeframe
                RETURNING css.consecutive_failures, css.symbol_id, css.source_id
                """
            ),
            {
                "symbol_code": source.symbol_code,
                "source_name": source.source_name,
                "timeframe": source.timeframe,
                "message": _short_error(message),
            },
        ).mappings().first()
        if row and int(row["consecutive_failures"]) >= 3:
            connection.execute(
                text(
                    """
                    INSERT INTO collector_incidents (
                        incident_key,
                        symbol_id,
                        source_id,
                        timeframe,
                        severity,
                        status,
                        title,
                        message,
                        failure_count,
                        first_seen_at,
                        last_seen_at,
                        metadata
                    )
                    VALUES (
                        :incident_key,
                        :symbol_id,
                        :source_id,
                        :timeframe,
                        'warning',
                        'open',
                        :title,
                        :message,
                        :failure_count,
                        now(),
                        now(),
                        CAST(:metadata AS jsonb)
                    )
                    ON CONFLICT (incident_key)
                    WHERE resolved_at IS NULL
                    DO UPDATE SET
                        status = 'open',
                        message = EXCLUDED.message,
                        failure_count = EXCLUDED.failure_count,
                        last_seen_at = now()
                    """
                ),
                {
                    "incident_key": _incident_key(source),
                    "symbol_id": row["symbol_id"],
                    "source_id": row["source_id"],
                    "timeframe": source.timeframe,
                    "title": f"{source.symbol_code} live collection failing",
                    "message": _short_error(message),
                    "failure_count": int(row["consecutive_failures"]),
                    "metadata": f'{{"provider":"{source.provider}"}}',
                },
            )


def resolve_remote_incident(remote_database_url: str, source: LiveSource) -> None:
    with build_engine(remote_database_url).begin() as connection:
        connection.execute(
            text(
                """
                UPDATE collector_incidents ci
                SET status = 'resolved', resolved_at = now(), last_seen_at = now()
                FROM symbols s, data_sources ds
                WHERE ci.symbol_id = s.id
                    AND ci.source_id = ds.id
                    AND ci.incident_key = :incident_key
                    AND s.symbol_code = :symbol_code
                    AND ds.name = :source_name
                    AND ci.timeframe = :timeframe
                    AND ci.resolved_at IS NULL
                """
            ),
            {
                "incident_key": _incident_key(source),
                "symbol_code": source.symbol_code,
                "source_name": source.source_name,
                "timeframe": source.timeframe,
            },
        )


def summarize_remote_collection(
    run_id: str,
    status: str,
    since: datetime,
    until: datetime,
    results: list[AssetCollectionResult],
) -> RemoteCollectionSummary:
    return RemoteCollectionSummary(
        run_id=run_id,
        status=status,
        assets_requested=len(results),
        assets_succeeded=sum(1 for result in results if result.status == "ok"),
        assets_failed=sum(1 for result in results if result.status == "error"),
        rows_fetched=sum(result.rows_fetched for result in results),
        rows_written=sum(result.rows_written for result in results),
        rows_inserted=sum(result.rows_inserted for result in results),
        rows_updated=sum(result.rows_updated for result in results),
        since=since,
        until=until,
        results=sorted(results, key=lambda result: result.symbol_code),
    )


def _selected_sources(config: str, symbols: Iterable[str] | None, max_priority: int | None = None) -> list[LiveSource]:
    symbol_set = {symbol.strip().upper() for symbol in symbols or [] if symbol.strip()}
    return [
        source
        for source in load_live_sources(config)
        if source.enabled
        and (max_priority is None or source.priority <= max_priority)
        and (not symbol_set or source.symbol_code.upper() in symbol_set)
    ]


def _collection_window(since: datetime | None, until: datetime | None) -> tuple[datetime, datetime]:
    if since and until:
        start = _utc_dt(since)
        end = _utc_dt(until)
    elif since or until:
        raise ValueError("Use both since and until, or neither.")
    else:
        start, end = previous_utc_day_window()
    if end <= start:
        raise ValueError("until must be greater than since.")
    return start, end


def _frame_chunks(frame: pd.DataFrame, rows: int):
    rows = max(1, rows)
    for start in range(0, len(frame), rows):
        yield frame.iloc[start : start + rows].copy()


def _merge_sync_results(results: Iterable[LiveSyncResult]) -> LiveSyncResult:
    grouped: dict[tuple[str, str, str, str], dict] = {}
    rows_read = rows_written = rows_inserted = rows_updated = 0
    for result in results:
        rows_read += result.rows_read
        rows_written += result.rows_written
        rows_inserted += result.rows_inserted
        rows_updated += result.rows_updated
        for group in result.groups:
            key = (
                str(group["symbol"]),
                str(group["source"]),
                str(group["source_symbol"]),
                str(group["timeframe"]),
            )
            target = grouped.setdefault(
                key,
                {
                    "symbol": group["symbol"],
                    "source": group["source"],
                    "source_symbol": group["source_symbol"],
                    "timeframe": group["timeframe"],
                    "rows": 0,
                    "inserted": 0,
                    "updated": 0,
                },
            )
            target["rows"] += int(group.get("rows") or 0)
            target["inserted"] += int(group.get("inserted") or 0)
            target["updated"] += int(group.get("updated") or 0)
    return LiveSyncResult(rows_read, rows_written, rows_inserted, rows_updated, list(grouped.values()))


def _utc_dt(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _short_error(exc: Exception | str, limit: int = 800) -> str:
    message = str(exc).replace("\n", " ")
    return message if len(message) <= limit else message[: limit - 3] + "..."


def _incident_key(source: LiveSource) -> str:
    return f"{source.symbol_code}:{source.source_name}:{source.timeframe}:collector_failure"


class JsonlEventLogger:
    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, **payload) -> None:
        row = {
            "event": event,
            "time": datetime.now(timezone.utc).isoformat(),
            **_json_log_safe(payload),
        }
        print(f"[collector:{event}] {json.dumps(row, sort_keys=True, default=str)}", flush=True)
        if self.path:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _run_metadata(
    trigger_type: str,
    since: datetime,
    until: datetime,
    symbols: Iterable[str] | None,
    max_priority: int | None,
) -> dict:
    return {
        "trigger_type": trigger_type,
        "github_run_id": os.getenv("GITHUB_RUN_ID"),
        "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
        "github_workflow": os.getenv("GITHUB_WORKFLOW"),
        "github_job": os.getenv("GITHUB_JOB"),
        "github_ref": os.getenv("GITHUB_REF"),
        "github_sha": os.getenv("GITHUB_SHA"),
        "github_actor": os.getenv("GITHUB_ACTOR"),
        "symbols": list(symbols or []),
        "max_priority": max_priority,
        "window": {"since": since.isoformat(), "until": until.isoformat()},
    }


def _source_log_payload(source: LiveSource) -> dict:
    return {
        "symbol_code": source.symbol_code,
        "source_name": source.source_name,
        "source_symbol": source.source_symbol,
        "provider": source.provider,
        "timeframe": source.timeframe,
        "priority": source.priority,
    }


def _summary_log_payload(summary: RemoteCollectionSummary) -> dict:
    return {
        "run_id": summary.run_id,
        "status": summary.status,
        "assets_requested": summary.assets_requested,
        "assets_succeeded": summary.assets_succeeded,
        "assets_failed": summary.assets_failed,
        "rows_fetched": summary.rows_fetched,
        "rows_written": summary.rows_written,
        "rows_inserted": summary.rows_inserted,
        "rows_updated": summary.rows_updated,
        "since": summary.since.isoformat(),
        "until": summary.until.isoformat(),
    }


def _json_log_safe(value):
    if isinstance(value, dict):
        return {key: _json_log_safe(item) for key, item in value.items() if "url" not in key.lower()}
    if isinstance(value, list):
        return [_json_log_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value
