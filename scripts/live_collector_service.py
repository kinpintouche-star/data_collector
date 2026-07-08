from __future__ import annotations

import os
import signal
import time
from datetime import datetime, timedelta, timezone
from typing import Iterable

from ict.core.config import get_settings
from ict.live.collector import collect_remote_live


STOP_REQUESTED = False


def main() -> None:
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    mode = os.getenv("LIVE_COLLECTOR_MODE", "daily").strip().lower()
    run_on_start = _bool_env("LIVE_COLLECTOR_RUN_ON_START", default=True)
    interval_seconds = _int_env("LIVE_COLLECTOR_INTERVAL_SECONDS", default=0)
    daily_hour = _int_env("LIVE_COLLECTOR_DAILY_UTC_HOUR", default=2)
    daily_minute = _int_env("LIVE_COLLECTOR_DAILY_UTC_MINUTE", default=17)

    print(
        "[collector] docker scheduler started "
        f"mode={mode} run_on_start={run_on_start} interval_seconds={interval_seconds} "
        f"daily_utc={daily_hour:02d}:{daily_minute:02d}",
        flush=True,
    )

    if run_on_start:
        run_collection_batch(mode)

    while not STOP_REQUESTED:
        now = datetime.now(timezone.utc)
        if mode == "rolling" or interval_seconds > 0:
            interval_seconds = interval_seconds if interval_seconds > 0 else 3600
            next_run = now + timedelta(seconds=interval_seconds)
        else:
            next_run = _next_daily_run(now, daily_hour, daily_minute)
        _sleep_until(next_run)
        if not STOP_REQUESTED:
            run_collection_batch(mode)


def run_collection_batch(mode: str) -> None:
    remote_url = os.getenv("LIVE_REMOTE_DATABASE_URL") or get_settings().live_remote_database_url
    if not remote_url:
        print("[collector] LIVE_REMOTE_DATABASE_URL is not configured; skipping run.", flush=True)
        return

    backfill_days = max(1, _int_env("LIVE_COLLECTOR_BACKFILL_DAYS", default=3))
    symbols = _csv_env("LIVE_COLLECTOR_SYMBOLS")
    config = os.getenv("LIVE_COLLECTOR_CONFIG", "configs/live_sources.yaml")
    max_workers = _int_env("LIVE_COLLECTOR_MAX_WORKERS", default=4)
    upload_chunk_rows = _int_env("LIVE_COLLECTOR_UPLOAD_CHUNK_ROWS", default=2500)
    submit_pause_seconds = _float_env("LIVE_COLLECTOR_SUBMIT_PAUSE_SECONDS", default=0.35)

    windows = _collection_windows(mode, backfill_days)
    print(
        "[collector] starting batch "
        f"mode={mode} windows={len(windows)} symbols={','.join(symbols) if symbols else 'all'}",
        flush=True,
    )
    for window_start, window_end, trigger_type in windows:
        if STOP_REQUESTED:
            return
        try:
            result = collect_remote_live(
                remote_url,
                since=window_start,
                until=window_end,
                symbols=symbols or None,
                config=config,
                max_workers=max_workers,
                upload_chunk_rows=upload_chunk_rows,
                trigger_type=trigger_type,
                submit_pause_seconds=submit_pause_seconds,
            )
            print(
                "[collector] completed "
                f"window={window_start.isoformat()}->{window_end.isoformat()} "
                f"assets={result.assets_requested} ok={result.assets_succeeded} "
                f"failed={result.assets_failed} rows={result.rows_written}",
                flush=True,
            )
        except Exception as exc:
            print(
                "[collector] failed "
                f"window={window_start.isoformat()}->{window_end.isoformat()} error={_compact_error(exc)}",
                flush=True,
            )


def _collection_windows(mode: str, backfill_days: int) -> list[tuple[datetime, datetime, str]]:
    if mode == "rolling":
        lookback_minutes = max(1, _int_env("LIVE_COLLECTOR_LOOKBACK_MINUTES", default=180))
        safety_delay_minutes = max(1, _int_env("LIVE_COLLECTOR_SAFETY_DELAY_MINUTES", default=3))
        window_end = _floor_minute(datetime.now(timezone.utc) - timedelta(minutes=safety_delay_minutes))
        window_start = window_end - timedelta(minutes=lookback_minutes)
        return [(window_start, window_end, "docker_rolling")]

    if mode != "daily":
        raise ValueError(f"Unsupported LIVE_COLLECTOR_MODE={mode!r}. Use 'rolling' or 'daily'.")

    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return [(day_start, day_start + timedelta(days=1), "docker_daily_repair") for day_start in _complete_day_starts(today, backfill_days)]


def _complete_day_starts(today_utc: datetime, backfill_days: int) -> Iterable[datetime]:
    first_day = today_utc - timedelta(days=backfill_days)
    for offset in range(backfill_days):
        yield first_day + timedelta(days=offset)


def _floor_minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def _next_daily_run(now: datetime, hour: int, minute: int) -> datetime:
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def _sleep_until(target: datetime) -> None:
    print(f"[collector] next run at {target.isoformat()}", flush=True)
    while not STOP_REQUESTED:
        remaining = (target - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 60))


def _request_stop(_signum: int, _frame: object) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True
    print("[collector] stop requested", flush=True)


def _csv_env(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _compact_error(exc: Exception, limit: int = 500) -> str:
    message = str(exc).replace("\n", " ")
    return message if len(message) <= limit else f"{message[:limit]}..."


if __name__ == "__main__":
    main()
