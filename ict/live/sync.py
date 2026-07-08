from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Iterable

import pandas as pd
from sqlalchemy import text

from ict.db.repositories import AliasRepository, CandleRepository
from ict.db.session import build_engine, build_sessionmaker, session_scope
from ict.live.config import load_live_sources


@dataclass(frozen=True)
class LiveSyncResult:
    rows_read: int
    rows_written: int
    rows_inserted: int
    rows_updated: int
    groups: list[dict]

    def as_dict(self) -> dict:
        return {
            "rows_read": self.rows_read,
            "rows_written": self.rows_written,
            "rows_inserted": self.rows_inserted,
            "rows_updated": self.rows_updated,
            "groups": self.groups,
        }


@dataclass(frozen=True)
class RemotePruneResult:
    dry_run: bool
    cutoff: datetime
    require_local: bool
    candidate_rows: int
    deleted_rows: int
    blocked_rows: int
    groups: list[dict]

    def as_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "cutoff": self.cutoff,
            "require_local": self.require_local,
            "candidate_rows": self.candidate_rows,
            "deleted_rows": self.deleted_rows,
            "blocked_rows": self.blocked_rows,
            "groups": self.groups,
        }


def sync_remote_candles(
    remote_database_url: str,
    since: datetime | None = None,
    until: datetime | None = None,
    symbols: Iterable[str] | None = None,
    limit: int = 50000,
) -> LiveSyncResult:
    frame = read_remote_candles(remote_database_url, since, until, symbols, limit)
    return upsert_remote_frame(frame)


def remote_storage_usage(remote_database_url: str) -> dict:
    size_query = text(
        """
        SELECT
            pg_relation_size('live_market_candles'::regclass)::bigint AS heap_bytes,
            (pg_indexes_size('live_market_candles'::regclass))::bigint AS index_bytes,
            pg_total_relation_size('live_market_candles'::regclass)::bigint AS total_bytes,
            COUNT(*)::bigint AS rows
        FROM live_market_candles
        """
    )
    source_query = text(
        """
        SELECT
            s.symbol_code,
            ds.name AS source_name,
            css.source_symbol,
            css.timeframe,
            css.enabled,
            css.collection_mode,
            COUNT(lmc.time_open)::bigint AS rows,
            MIN(lmc.time_open) AS first_candle_time,
            MAX(lmc.time_open) AS last_candle_time
        FROM collector_source_state css
        JOIN symbols s ON s.id = css.symbol_id
        JOIN data_sources ds ON ds.id = css.source_id
        LEFT JOIN live_market_candles lmc ON lmc.source_state_id = css.id
        GROUP BY s.symbol_code, ds.name, css.source_symbol, css.timeframe, css.enabled, css.collection_mode
        ORDER BY css.enabled DESC, s.symbol_code, ds.name, css.timeframe
        """
    )
    with build_engine(remote_database_url).connect() as connection:
        size = dict(connection.execute(size_query).mappings().one())
        sources = [dict(row) for row in connection.execute(source_query).mappings()]
    return {
        "live_market_candles": {
            "rows": int(size["rows"] or 0),
            "heap_bytes": int(size["heap_bytes"] or 0),
            "index_bytes": int(size["index_bytes"] or 0),
            "total_bytes": int(size["total_bytes"] or 0),
        },
        "sources": sources,
    }


def prune_remote_candles(
    remote_database_url: str,
    cutoff: datetime,
    symbols: Iterable[str] | None = None,
    require_local: bool = True,
    dry_run: bool = True,
) -> RemotePruneResult:
    cutoff = _utc_dt(cutoff)
    candidates = _remote_prune_candidates(remote_database_url, cutoff, symbols)
    groups = []
    deleted_rows = 0
    blocked_rows = 0
    with build_engine(remote_database_url).begin() as remote_connection:
        for candidate in candidates:
            local_rows = None
            safe_to_delete = True
            reason = None
            if require_local:
                local_rows = _count_local_candles_for_candidate(candidate, cutoff)
                safe_to_delete = local_rows >= int(candidate["rows"])
                reason = None if safe_to_delete else "local_copy_incomplete"

            deleted = 0
            if safe_to_delete and not dry_run:
                deleted = int(
                    remote_connection.execute(
                        text(
                            """
                            DELETE FROM live_market_candles
                            WHERE source_state_id = CAST(:source_state_id AS uuid)
                                AND time_open < :cutoff
                            """
                        ),
                        {"source_state_id": candidate["source_state_id"], "cutoff": cutoff},
                    ).rowcount
                    or 0
                )
                deleted_rows += deleted
            elif not safe_to_delete:
                blocked_rows += int(candidate["rows"])

            groups.append(
                {
                    "symbol_code": candidate["symbol_code"],
                    "source_name": candidate["source_name"],
                    "source_symbol": candidate["source_symbol"],
                    "timeframe": candidate["timeframe"],
                    "remote_rows": int(candidate["rows"]),
                    "local_rows": local_rows,
                    "first_candle_time": candidate["first_candle_time"],
                    "last_candle_time": candidate["last_candle_time"],
                    "safe_to_delete": safe_to_delete,
                    "dry_run": dry_run,
                    "deleted_rows": deleted,
                    "reason": reason,
                }
            )

    if deleted_rows:
        refresh_remote_source_state(remote_database_url, symbols=symbols)
    return RemotePruneResult(
        dry_run=dry_run,
        cutoff=cutoff,
        require_local=require_local,
        candidate_rows=sum(int(row["rows"]) for row in candidates),
        deleted_rows=deleted_rows,
        blocked_rows=blocked_rows,
        groups=groups,
    )


def sync_local_candles_to_remote(
    remote_database_url: str,
    since: datetime | None = None,
    until: datetime | None = None,
    symbols: Iterable[str] | None = None,
    limit: int = 250000,
    chunk_days: int = 7,
    retention_days: int = 30,
    config: str = "configs/live_sources.yaml",
) -> LiveSyncResult:
    end = _utc_dt(until) if until else datetime.now(timezone.utc)
    start = _utc_dt(since) if since else end - timedelta(days=retention_days)
    if chunk_days <= 0:
        raise ValueError("chunk_days must be greater than 0.")
    if limit <= 0:
        raise ValueError("limit must be greater than 0.")
    if end <= start:
        raise ValueError("until must be greater than since.")

    results: list[LiveSyncResult] = []
    for chunk_start, chunk_end in _chunk_ranges(start, end, chunk_days):
        frame = read_local_live_candles(
            since=chunk_start,
            until=chunk_end,
            symbols=symbols,
            limit=limit,
            config=config,
        )
        if len(frame) >= limit:
            raise ValueError(
                f"Chunk {chunk_start.isoformat()} -> {chunk_end.isoformat()} reached --limit={limit}. "
                "Reduce --chunk-days or increase --limit to avoid truncating the remote seed."
            )
        results.append(upsert_frame_to_remote_compact(frame, remote_database_url))
    result = _combine_results(results)
    if result.rows_written:
        refresh_remote_source_state(remote_database_url, symbols=symbols, config=config)
    return result


def _remote_prune_candidates(
    remote_database_url: str,
    cutoff: datetime,
    symbols: Iterable[str] | None,
) -> list[dict]:
    symbol_list = [symbol.strip().upper() for symbol in symbols or [] if symbol.strip()]
    params: dict[str, object] = {"cutoff": cutoff}
    symbol_filter = ""
    if symbol_list:
        placeholders = []
        for index, symbol in enumerate(symbol_list):
            key = f"symbol_{index}"
            placeholders.append(f":{key}")
            params[key] = symbol
        symbol_filter = f"AND s.symbol_code IN ({', '.join(placeholders)})"
    query = text(
        f"""
        SELECT
            css.id::text AS source_state_id,
            s.symbol_code,
            ds.name AS source_name,
            css.source_symbol,
            css.timeframe,
            COUNT(lmc.time_open)::bigint AS rows,
            MIN(lmc.time_open) AS first_candle_time,
            MAX(lmc.time_open) AS last_candle_time
        FROM live_market_candles lmc
        JOIN collector_source_state css ON css.id = lmc.source_state_id
        JOIN symbols s ON s.id = css.symbol_id
        JOIN data_sources ds ON ds.id = css.source_id
        WHERE lmc.time_open < :cutoff
            {symbol_filter}
        GROUP BY css.id, s.symbol_code, ds.name, css.source_symbol, css.timeframe
        HAVING COUNT(lmc.time_open) > 0
        ORDER BY s.symbol_code, ds.name, css.timeframe
        """
    )
    with build_engine(remote_database_url).connect() as connection:
        return [dict(row) for row in connection.execute(query, params).mappings()]


def _count_local_candles_for_candidate(candidate: dict, cutoff: datetime) -> int:
    query = text(
        """
        SELECT COUNT(c.id)::bigint
        FROM market_candles c
        JOIN symbols s ON s.id = c.symbol_id
        JOIN data_sources ds ON ds.id = c.source_id
        WHERE s.symbol_code = :symbol_code
            AND ds.name = :source_name
            AND c.source_symbol = :source_symbol
            AND c.timeframe = :timeframe
            AND c.time_open < :cutoff
        """
    )
    with build_engine().connect() as connection:
        return int(
            connection.execute(
                query,
                {
                    "symbol_code": candidate["symbol_code"],
                    "source_name": candidate["source_name"],
                    "source_symbol": candidate["source_symbol"],
                    "timeframe": str(candidate["timeframe"]).upper(),
                    "cutoff": cutoff,
                },
            ).scalar_one()
            or 0
        )


def read_remote_candles(
    remote_database_url: str,
    since: datetime | None,
    until: datetime | None,
    symbols: Iterable[str] | None,
    limit: int,
) -> pd.DataFrame:
    symbol_list = list(symbols or [])
    conditions = []
    params: dict[str, object] = {"limit": int(limit)}
    if since is not None:
        conditions.append("lmc.time_open >= :since")
        params["since"] = _utc_dt(since)
    if until is not None:
        conditions.append("lmc.time_open <= :until")
        params["until"] = _utc_dt(until)
    for index, symbol in enumerate(symbol_list):
        key = f"symbol_{index}"
        conditions.append(f"s.symbol_code = :{key}")
        params[key] = symbol

    where = f"WHERE {' OR '.join(conditions)}" if symbol_list else f"WHERE {' AND '.join(conditions)}" if conditions else ""
    if symbol_list and (since is not None or until is not None):
        time_conditions = []
        if since is not None:
            time_conditions.append("lmc.time_open >= :since")
        if until is not None:
            time_conditions.append("lmc.time_open <= :until")
        symbol_conditions = [f"s.symbol_code = :symbol_{idx}" for idx, _ in enumerate(symbol_list)]
        where = f"WHERE ({' AND '.join(time_conditions)}) AND ({' OR '.join(symbol_conditions)})"

    query = text(
        f"""
        SELECT
            s.symbol_code,
            ds.name AS source_name,
            css.source_symbol,
            css.timeframe,
            lmc.time_open,
            lmc.open,
            lmc.high,
            lmc.low,
            lmc.close,
            lmc.tick_volume,
            lmc.real_volume,
            lmc.spread,
            '{{}}'::jsonb AS quality_flags,
            '{{}}'::jsonb AS metadata
        FROM live_market_candles lmc
        JOIN collector_source_state css ON css.id = lmc.source_state_id
        JOIN symbols s ON s.id = css.symbol_id
        JOIN data_sources ds ON ds.id = css.source_id
        {where}
        ORDER BY lmc.time_open
        LIMIT :limit
        """
    )
    return pd.read_sql(query, build_engine(remote_database_url), params=params)


def read_local_live_candles(
    since: datetime,
    until: datetime,
    symbols: Iterable[str] | None,
    limit: int,
    config: str = "configs/live_sources.yaml",
) -> pd.DataFrame:
    source_filters = _live_source_filters(config, symbols)
    if not source_filters:
        return _empty_candle_frame()

    conditions = ["c.time_open >= :since", "c.time_open < :until"]
    params: dict[str, object] = {
        "since": _utc_dt(since),
        "until": _utc_dt(until),
        "limit": int(limit),
    }
    source_conditions = []
    for index, source in enumerate(source_filters):
        source_conditions.append(
            f"(s.symbol_code = :symbol_code_{index} "
            f"AND ds.name = :source_name_{index} "
            f"AND c.source_symbol = :source_symbol_{index} "
            f"AND c.timeframe = :timeframe_{index})"
        )
        params[f"symbol_code_{index}"] = source.symbol_code
        params[f"source_name_{index}"] = source.source_name
        params[f"source_symbol_{index}"] = source.source_symbol
        params[f"timeframe_{index}"] = source.timeframe.upper()
    conditions.append(f"({' OR '.join(source_conditions)})")

    query = text(
        f"""
        SELECT
            s.symbol_code,
            ds.name AS source_name,
            c.source_symbol,
            c.timeframe,
            c.time_open,
            c.open,
            c.high,
            c.low,
            c.close,
            c.tick_volume,
            c.real_volume,
            c.spread,
            c.quality_flags,
            c.metadata
        FROM market_candles c
        JOIN symbols s ON s.id = c.symbol_id
        JOIN data_sources ds ON ds.id = c.source_id
        WHERE {' AND '.join(conditions)}
        ORDER BY c.time_open
        LIMIT :limit
        """
    )
    return pd.read_sql(query, build_engine(), params=params)


def upsert_remote_frame(frame: pd.DataFrame) -> LiveSyncResult:
    return _upsert_frame_with_session(frame, session_scope)


def upsert_frame_to_database(frame: pd.DataFrame, database_url: str) -> LiveSyncResult:
    return _upsert_frame_with_session(frame, lambda: _database_session_scope(database_url))


def upsert_frame_to_remote_compact(frame: pd.DataFrame, remote_database_url: str) -> LiveSyncResult:
    if frame.empty:
        return LiveSyncResult(0, 0, 0, 0, [])

    groups = []
    rows_written = 0
    rows_inserted = 0
    rows_updated = 0
    with build_engine(remote_database_url).begin() as connection:
        grouped = frame.groupby(["symbol_code", "source_name", "source_symbol", "timeframe"], dropna=False)
        for (symbol_code, source_name, source_symbol, timeframe), group in grouped:
            state_id = connection.execute(
                text(
                    """
                    SELECT css.id
                    FROM collector_source_state css
                    JOIN symbols s ON s.id = css.symbol_id
                    JOIN data_sources ds ON ds.id = css.source_id
                    WHERE s.symbol_code = :symbol_code
                        AND ds.name = :source_name
                        AND css.source_symbol = :source_symbol
                        AND css.timeframe = :timeframe
                    LIMIT 1
                    """
                ),
                {
                    "symbol_code": str(symbol_code),
                    "source_name": str(source_name),
                    "source_symbol": str(source_symbol),
                    "timeframe": str(timeframe).upper(),
                },
            ).scalar_one_or_none()
            if state_id is None:
                raise ValueError(f"Remote live source not registered: {symbol_code}/{source_name}/{timeframe}.")

            written, inserted = _upsert_compact_group(connection, str(state_id), group)
            updated = max(0, written - inserted)
            rows_written += written
            rows_inserted += inserted
            rows_updated += updated
            groups.append(
                {
                    "symbol": symbol_code,
                    "source": source_name,
                    "source_symbol": source_symbol,
                    "timeframe": str(timeframe).upper(),
                    "rows": written,
                    "inserted": inserted,
                    "updated": updated,
                }
            )

    return LiveSyncResult(
        rows_read=int(len(frame)),
        rows_written=rows_written,
        rows_inserted=rows_inserted,
        rows_updated=rows_updated,
        groups=groups,
    )


def _upsert_compact_group(connection, source_state_id: str, group: pd.DataFrame) -> tuple[int, int]:
    payload = json.dumps(_compact_payload_records(group), default=str)
    rows = connection.execute(
        text(
            """
            WITH payload AS (
                SELECT *
                FROM jsonb_to_recordset(CAST(:payload AS jsonb)) AS x(
                    time_open timestamptz,
                    open double precision,
                    high double precision,
                    low double precision,
                    close double precision,
                    tick_volume bigint,
                    real_volume double precision,
                    spread double precision
                )
            )
            INSERT INTO live_market_candles (
                source_state_id,
                time_open,
                open,
                high,
                low,
                close,
                tick_volume,
                real_volume,
                spread,
                ingested_at
            )
            SELECT
                CAST(:source_state_id AS uuid),
                time_open,
                open,
                high,
                low,
                close,
                tick_volume,
                real_volume,
                spread,
                now()
            FROM payload
            ON CONFLICT (source_state_id, time_open)
            DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                tick_volume = EXCLUDED.tick_volume,
                real_volume = EXCLUDED.real_volume,
                spread = EXCLUDED.spread,
                ingested_at = now()
            RETURNING (xmax = 0) AS inserted
            """
        ),
        {"payload": payload, "source_state_id": source_state_id},
    ).mappings()
    flags = [bool(row["inserted"]) for row in rows]
    return len(flags), sum(1 for value in flags if value)


def _compact_payload_records(group: pd.DataFrame) -> list[dict]:
    records = []
    for row in group.to_dict(orient="records"):
        records.append(
            {
                "time_open": pd.Timestamp(row["time_open"]).isoformat(),
                "open": _nullable_float(row.get("open")),
                "high": _nullable_float(row.get("high")),
                "low": _nullable_float(row.get("low")),
                "close": _nullable_float(row.get("close")),
                "tick_volume": _nullable_int(row.get("tick_volume")),
                "real_volume": _nullable_float(row.get("real_volume")),
                "spread": _nullable_float(row.get("spread")),
            }
        )
    return records


def refresh_remote_source_state(
    remote_database_url: str,
    symbols: Iterable[str] | None = None,
    config: str = "configs/live_sources.yaml",
) -> int:
    sources = _live_source_filters(config, symbols)
    updated = 0
    query = text(
        """
        WITH latest AS (
            SELECT MAX(lmc.time_open) AS last_candle_time
            FROM live_market_candles lmc
            JOIN collector_source_state state ON state.id = lmc.source_state_id
            JOIN symbols s ON s.id = state.symbol_id
            JOIN data_sources ds ON ds.id = state.source_id
            WHERE s.symbol_code = :symbol_code
                AND ds.name = :source_name
                AND state.source_symbol = :source_symbol
                AND state.timeframe = :timeframe
        )
        UPDATE collector_source_state css
        SET
            last_candle_time = latest.last_candle_time,
            last_success_at = CASE
                WHEN latest.last_candle_time IS NULL THEN css.last_success_at
                ELSE now()
            END,
            status = CASE
                WHEN latest.last_candle_time IS NULL THEN css.status
                ELSE 'ready'
            END,
            lag_seconds = CASE
                WHEN latest.last_candle_time IS NULL THEN css.lag_seconds
                ELSE GREATEST(0, EXTRACT(EPOCH FROM (now() - latest.last_candle_time))::integer)
            END,
            consecutive_failures = CASE
                WHEN latest.last_candle_time IS NULL THEN css.consecutive_failures
                ELSE 0
            END,
            updated_at = now()
        FROM latest
        JOIN symbols s ON s.symbol_code = :symbol_code
        JOIN data_sources ds ON ds.name = :source_name
        WHERE css.symbol_id = s.id
            AND css.source_id = ds.id
            AND css.timeframe = :timeframe
        """
    )
    with build_engine(remote_database_url).begin() as connection:
        for source in sources:
            result = connection.execute(
                query,
                {
                    "symbol_code": source.symbol_code,
                    "source_name": source.source_name,
                    "source_symbol": source.source_symbol,
                    "timeframe": source.timeframe.upper(),
                },
            )
            updated += int(result.rowcount or 0)
    return updated


@contextmanager
def _database_session_scope(database_url: str):
    session = build_sessionmaker(database_url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _upsert_frame_with_session(
    frame: pd.DataFrame,
    session_scope_factory: Callable[[], object],
) -> LiveSyncResult:
    if frame.empty:
        return LiveSyncResult(0, 0, 0, 0, [])

    groups = []
    rows_written = 0
    rows_inserted = 0
    rows_updated = 0

    with session_scope_factory() as session:
        aliases = AliasRepository(session)
        candles = CandleRepository(session)
        grouped = frame.groupby(["symbol_code", "source_name", "source_symbol", "timeframe"], dropna=False)
        for (symbol_code, source_name, source_symbol, timeframe), group in grouped:
            alias = aliases.resolve(str(symbol_code), str(source_name))
            storage_frame = group.rename(columns={"metadata": "source_metadata"}).copy()
            storage_frame["quality_flags"] = storage_frame["quality_flags"].map(lambda value: value or {})
            storage_frame["source_metadata"] = storage_frame["source_metadata"].map(lambda value: value or {})
            rows = candles.rows_for_frame(
                alias.symbol_id,
                alias.source_id,
                str(source_symbol),
                str(timeframe),
                storage_frame,
            )
            existing = candles.count_existing_candles(
                alias.symbol_id,
                alias.source_id,
                str(timeframe).upper(),
                [row["time_open"] for row in rows],
            )
            written = candles.upsert_candles(rows)
            inserted = max(0, len(rows) - existing)
            updated = min(existing, len(rows))
            rows_written += written
            rows_inserted += inserted
            rows_updated += updated
            groups.append(
                {
                    "symbol": symbol_code,
                    "source": source_name,
                    "source_symbol": source_symbol,
                    "timeframe": str(timeframe).upper(),
                    "rows": len(rows),
                    "inserted": inserted,
                    "updated": updated,
                }
            )

    return LiveSyncResult(
        rows_read=int(len(frame)),
        rows_written=rows_written,
        rows_inserted=rows_inserted,
        rows_updated=rows_updated,
        groups=groups,
    )


def _utc_dt(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _nullable_float(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _nullable_int(value) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _live_source_filters(config: str, symbols: Iterable[str] | None):
    symbol_set = {symbol.strip().upper() for symbol in symbols or [] if symbol.strip()}
    rows = []
    for source in load_live_sources(config):
        if not source.enabled:
            continue
        if symbol_set and source.symbol_code.upper() not in symbol_set:
            continue
        rows.append(source)
    return rows


def _chunk_ranges(start: datetime, end: datetime, chunk_days: int) -> list[tuple[datetime, datetime]]:
    ranges = []
    current = start
    step = timedelta(days=chunk_days)
    while current < end:
        chunk_end = min(current + step, end)
        ranges.append((current, chunk_end))
        current = chunk_end
    return ranges


def _combine_results(results: Iterable[LiveSyncResult]) -> LiveSyncResult:
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

    return LiveSyncResult(
        rows_read=rows_read,
        rows_written=rows_written,
        rows_inserted=rows_inserted,
        rows_updated=rows_updated,
        groups=list(grouped.values()),
    )


def _empty_candle_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "symbol_code",
            "source_name",
            "source_symbol",
            "timeframe",
            "time_open",
            "open",
            "high",
            "low",
            "close",
            "tick_volume",
            "real_volume",
            "spread",
            "quality_flags",
            "metadata",
        ]
    )
