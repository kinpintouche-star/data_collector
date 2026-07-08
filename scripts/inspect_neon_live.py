from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from ict.core.config import get_settings
from ict.db.session import build_engine


def _rows(connection, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(text(query), params or {}).mappings()]


def main() -> None:
    remote_url = get_settings().live_remote_database_url
    if not remote_url:
        raise SystemExit("LIVE_REMOTE_DATABASE_URL is not configured.")

    payload: dict[str, Any] = {"generated_at": datetime.now(timezone.utc).isoformat()}
    engine = build_engine(remote_url)
    with engine.connect() as connection:
        payload["collector_runs_summary"] = _rows(
            connection,
            """
            SELECT
                COUNT(*)::integer AS runs,
                MIN(started_at) AS first_started_at,
                MAX(started_at) AS last_started_at,
                COUNT(*) FILTER (WHERE status IN ('ok', 'completed'))::integer AS successful_runs,
                COUNT(*) FILTER (WHERE status NOT IN ('ok', 'completed'))::integer AS non_success_runs
            FROM collector_runs
            """,
        )[0]
        payload["collector_runs_by_day"] = _rows(
            connection,
            """
            SELECT
                started_at::date AS day,
                COUNT(*)::integer AS runs,
                COUNT(*) FILTER (WHERE status IN ('ok', 'completed'))::integer AS successful_runs,
                COUNT(*) FILTER (WHERE status NOT IN ('ok', 'completed'))::integer AS non_success_runs,
                SUM(COALESCE(rows_written, 0))::integer AS rows_written
            FROM collector_runs
            WHERE started_at >= now() - interval '14 days'
            GROUP BY started_at::date
            ORDER BY day DESC
            """,
        )
        payload["collector_runs_by_trigger"] = _rows(
            connection,
            """
            SELECT
                trigger_type,
                COUNT(*)::integer AS runs,
                COUNT(*) FILTER (WHERE status IN ('ok', 'completed'))::integer AS successful_runs,
                COUNT(*) FILTER (WHERE status NOT IN ('ok', 'completed'))::integer AS non_success_runs,
                MIN(started_at) AS first_started_at,
                MAX(started_at) AS last_started_at,
                SUM(COALESCE(rows_written, 0))::integer AS rows_written
            FROM collector_runs
            GROUP BY trigger_type
            ORDER BY runs DESC, trigger_type
            """,
        )
        payload["recent_collector_runs"] = _rows(
            connection,
            """
            SELECT
                id,
                trigger_type,
                status,
                started_at,
                finished_at,
                assets_requested,
                assets_succeeded,
                assets_failed,
                rows_fetched,
                rows_written,
                metadata->>'since' AS since,
                metadata->>'until' AS until,
                error_message
            FROM collector_runs
            ORDER BY started_at DESC
            LIMIT 25
            """,
        )
        payload["recent_non_success_runs"] = _rows(
            connection,
            """
            SELECT
                id,
                trigger_type,
                status,
                started_at,
                finished_at,
                assets_requested,
                assets_succeeded,
                assets_failed,
                rows_written,
                error_message
            FROM collector_runs
            WHERE status NOT IN ('ok', 'completed')
            ORDER BY started_at DESC
            LIMIT 20
            """,
        )
        payload["open_incidents"] = _rows(
            connection,
            """
            SELECT
                ci.incident_key,
                s.symbol_code,
                ds.name AS source_name,
                ci.timeframe,
                ci.severity,
                ci.status,
                ci.title,
                ci.failure_count,
                ci.first_seen_at,
                ci.last_seen_at
            FROM collector_incidents ci
            LEFT JOIN symbols s ON s.id = ci.symbol_id
            LEFT JOIN data_sources ds ON ds.id = ci.source_id
            WHERE ci.status = 'open'
            ORDER BY ci.last_seen_at DESC
            LIMIT 100
            """,
        )
        payload["source_state"] = _rows(
            connection,
            """
            SELECT
                s.symbol_code,
                ds.name AS source_name,
                css.provider,
                css.enabled,
                css.status,
                css.last_candle_time,
                css.last_success_at,
                css.last_error_at,
                css.consecutive_failures,
                css.lag_seconds
            FROM collector_source_state css
            JOIN symbols s ON s.id = css.symbol_id
            JOIN data_sources ds ON ds.id = css.source_id
            ORDER BY css.enabled DESC, css.priority, s.symbol_code, ds.name
            """,
        )
        payload["remote_candles_by_day"] = _rows(
            connection,
            """
            SELECT
                lmc.time_open::date AS day,
                COUNT(*)::integer AS candles,
                COUNT(DISTINCT lmc.source_state_id)::integer AS active_sources,
                MIN(lmc.time_open) AS first_candle,
                MAX(lmc.time_open) AS last_candle
            FROM live_market_candles lmc
            WHERE lmc.time_open >= now() - interval '14 days'
            GROUP BY lmc.time_open::date
            ORDER BY day DESC
            """,
        )
        payload["previous_complete_day_by_symbol"] = _rows(
            connection,
            """
            WITH previous_day AS (
                SELECT (date_trunc('day', now() AT TIME ZONE 'UTC') - interval '1 day')::date AS day
            )
            SELECT
                s.symbol_code,
                ds.name AS source_name,
                COUNT(lmc.time_open)::integer AS candles,
                MIN(lmc.time_open) AS first_candle,
                MAX(lmc.time_open) AS last_candle,
                GREATEST(0, 1440 - COUNT(lmc.time_open))::integer AS missing_m1_estimate
            FROM collector_source_state css
            JOIN previous_day ON true
            JOIN symbols s ON s.id = css.symbol_id
            JOIN data_sources ds ON ds.id = css.source_id
            LEFT JOIN live_market_candles lmc
                ON lmc.source_state_id = css.id
                AND lmc.time_open >= previous_day.day
                AND lmc.time_open < previous_day.day + interval '1 day'
            WHERE css.enabled
            GROUP BY s.symbol_code, ds.name
            ORDER BY s.symbol_code, ds.name
            """,
        )
        payload["remote_last_by_symbol"] = _rows(
            connection,
            """
            SELECT
                s.symbol_code,
                ds.name AS source_name,
                COUNT(lmc.time_open)::integer AS candles,
                MIN(lmc.time_open) AS first_candle,
                MAX(lmc.time_open) AS last_candle
            FROM collector_source_state css
            JOIN symbols s ON s.id = css.symbol_id
            JOIN data_sources ds ON ds.id = css.source_id
            LEFT JOIN live_market_candles lmc ON lmc.source_state_id = css.id
            GROUP BY s.symbol_code, ds.name
            ORDER BY s.symbol_code, ds.name
            """,
        )

    print(json.dumps(payload, default=str, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
