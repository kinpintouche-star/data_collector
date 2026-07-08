from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from ict.core.config import get_settings
from ict.data.providers.databento_provider import DatabentoHistoricalProvider
from ict.live.config import LiveSource

M1 = timedelta(minutes=1)
M1_SECONDS = 60
COINBASE_MAX_CANDLES = 300
KRAKEN_MAX_PAGES = 6
OANDA_MAX_CANDLES = 5000
OANDA_PRACTICE_URL = "https://api-fxpractice.oanda.com"
OANDA_LIVE_URL = "https://api-fxtrade.oanda.com"


class LiveProviderError(RuntimeError):
    pass


def previous_utc_day_window(now: datetime | None = None, overlap_minutes: int = 15) -> tuple[datetime, datetime]:
    current = _utc_dt(now or datetime.now(timezone.utc))
    today = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=1, minutes=overlap_minutes), today


def fetch_live_source(source: LiveSource, since: datetime, until: datetime, now: datetime | None = None) -> pd.DataFrame:
    if source.provider == "coinbase":
        try:
            frame = fetch_coinbase_candles(source, since, until, now=now)
            if not frame.empty or source.fallback_provider != "kraken":
                return frame
        except Exception as exc:
            if source.fallback_provider != "kraken":
                raise
            coinbase_error = exc
        else:
            coinbase_error = LiveProviderError("Coinbase returned no closed candles.")
        try:
            frame = fetch_kraken_candles(source, since, until, now=now)
            if not frame.empty:
                return frame
            raise LiveProviderError("Kraken returned no closed candles.")
        except Exception as exc:
            raise LiveProviderError(
                f"Coinbase failed: {_short_error(coinbase_error)}; Kraken fallback failed: {_short_error(exc)}"
            ) from exc
    if source.provider == "kraken":
        return fetch_kraken_candles(source, since, until, now=now)
    if source.provider == "oanda":
        return fetch_oanda_candles(source, since, until, now=now)
    if source.provider == "dukascopy_node":
        return fetch_dukascopy_node_candles(source, since, until, now=now)
    if source.provider == "databento":
        return fetch_databento_candles(source, since, until, now=now)
    if source.provider == "pending_cloud_source":
        raise LiveProviderError(f"{source.symbol_code} has no cloud-compatible live source yet.")
    raise LiveProviderError(f"Unsupported live provider: {source.provider}.")


def fetch_coinbase_candles(
    source: LiveSource,
    since: datetime,
    until: datetime,
    now: datetime | None = None,
    base_url: str = "https://api.exchange.coinbase.com",
) -> pd.DataFrame:
    rows = []
    cursor = _utc_dt(since)
    end = _utc_dt(until)
    while cursor < end:
        page_end = min(end, cursor + COINBASE_MAX_CANDLES * M1)
        params = urlencode(
            {
                "granularity": M1_SECONDS,
                "start": cursor.isoformat().replace("+00:00", "Z"),
                "end": page_end.isoformat().replace("+00:00", "Z"),
            }
        )
        url = f"{base_url.rstrip('/')}/products/{_provider_symbol(source)}/candles?{params}"
        page = _http_json(url)
        if not isinstance(page, list):
            raise LiveProviderError(f"Coinbase {_provider_symbol(source)} returned a non-list payload.")
        rows.extend(page)
        cursor = page_end
        time.sleep(0.05)
    return normalize_coinbase_rows(source, rows, since, until, now=now)


def normalize_coinbase_rows(
    source: LiveSource,
    rows: list,
    since: datetime,
    until: datetime,
    now: datetime | None = None,
) -> pd.DataFrame:
    current = _utc_dt(now or datetime.now(timezone.utc))
    start = _utc_dt(since)
    end = _utc_dt(until)
    records = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        time_open = datetime.fromtimestamp(float(row[0]), tz=timezone.utc)
        if not (start <= time_open < end) or time_open + M1 > current:
            continue
        records.append(
            {
                "symbol_code": source.symbol_code,
                "source_name": source.source_name,
                "source_symbol": source.source_symbol,
                "timeframe": source.timeframe,
                "time_open": time_open,
                "open": float(row[3]),
                "high": float(row[2]),
                "low": float(row[1]),
                "close": float(row[4]),
                "tick_volume": None,
                "real_volume": float(row[5]),
                "spread": None,
                "quality_flags": {},
                "metadata": {"provider": "coinbase", "provider_symbol": _provider_symbol(source)},
            }
        )
    return _frame_from_records(records)


def fetch_kraken_candles(
    source: LiveSource,
    since: datetime,
    until: datetime,
    now: datetime | None = None,
    base_url: str = "https://api.kraken.com",
) -> pd.DataFrame:
    rows = []
    cursor = _utc_dt(since)
    end = _utc_dt(until)
    pair = source.fallback_provider_symbol or source.provider_symbol or source.source_symbol
    for _ in range(KRAKEN_MAX_PAGES):
        if cursor >= end:
            break
        params = urlencode({"pair": pair, "interval": 1, "since": int(cursor.timestamp())})
        payload = _http_json(f"{base_url.rstrip('/')}/0/public/OHLC?{params}")
        errors = payload.get("error") if isinstance(payload, dict) else None
        if errors:
            raise LiveProviderError(f"Kraken {pair} failed: {', '.join(errors)}")
        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        key = next((item for item in result if item != "last"), None)
        page = result.get(key, []) if key else []
        if not page:
            break
        rows.extend(page)
        last_open = max(datetime.fromtimestamp(float(row[0]), tz=timezone.utc) for row in page)
        next_cursor = last_open + M1
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        time.sleep(0.05)
    return normalize_kraken_rows(source, rows, since, until, pair, now=now)


def normalize_kraken_rows(
    source: LiveSource,
    rows: list,
    since: datetime,
    until: datetime,
    provider_pair: str,
    now: datetime | None = None,
) -> pd.DataFrame:
    current = _utc_dt(now or datetime.now(timezone.utc))
    start = _utc_dt(since)
    end = _utc_dt(until)
    records = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 8:
            continue
        time_open = datetime.fromtimestamp(float(row[0]), tz=timezone.utc)
        if not (start <= time_open < end) or time_open + M1 > current:
            continue
        records.append(
            {
                "symbol_code": source.symbol_code,
                "source_name": source.source_name,
                "source_symbol": source.source_symbol,
                "timeframe": source.timeframe,
                "time_open": time_open,
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "tick_volume": int(row[7]),
                "real_volume": float(row[6]),
                "spread": None,
                "quality_flags": {},
                "metadata": {"provider": "kraken", "provider_symbol": provider_pair, "vwap": row[5]},
            }
        )
    return _frame_from_records(records)


def fetch_oanda_candles(
    source: LiveSource,
    since: datetime,
    until: datetime,
    now: datetime | None = None,
    base_url: str | None = None,
    token: str | None = None,
    price_component: str = "M",
) -> pd.DataFrame:
    token = token or _oanda_api_token()
    base_url = (base_url or _oanda_api_url()).rstrip("/")
    rows = []
    cursor = _utc_dt(since)
    end = _utc_dt(until)
    instrument = _provider_symbol(source)
    while cursor < end:
        page_end = min(end, cursor + OANDA_MAX_CANDLES * M1)
        params = urlencode(
            {
                "price": price_component,
                "granularity": "M1",
                "from": cursor.isoformat().replace("+00:00", "Z"),
                "to": page_end.isoformat().replace("+00:00", "Z"),
                "includeFirst": "true",
            }
        )
        url = f"{base_url}/v3/instruments/{instrument}/candles?{params}"
        payload = _http_json(url, headers={"Authorization": f"Bearer {token}"})
        candles = payload.get("candles") if isinstance(payload, dict) else None
        if not isinstance(candles, list):
            raise LiveProviderError(f"OANDA {instrument} returned no candles list.")
        rows.extend(candles)
        cursor = page_end
        time.sleep(0.05)
    return normalize_oanda_rows(source, rows, since, until, now=now, price_component=price_component)


def normalize_oanda_rows(
    source: LiveSource,
    rows: list,
    since: datetime,
    until: datetime,
    now: datetime | None = None,
    price_component: str = "M",
) -> pd.DataFrame:
    current = _utc_dt(now or datetime.now(timezone.utc))
    start = _utc_dt(since)
    end = _utc_dt(until)
    records = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("complete"):
            continue
        time_open = _parse_oanda_time(str(row.get("time", "")))
        if not (start <= time_open < end) or time_open + M1 > current:
            continue
        price = _oanda_price_bucket(row, price_component)
        records.append(
            {
                "symbol_code": source.symbol_code,
                "source_name": source.source_name,
                "source_symbol": source.source_symbol,
                "timeframe": source.timeframe,
                "time_open": time_open,
                "open": float(price["o"]),
                "high": float(price["h"]),
                "low": float(price["l"]),
                "close": float(price["c"]),
                "tick_volume": int(row["volume"]) if row.get("volume") is not None else None,
                "real_volume": None,
                "spread": None,
                "quality_flags": {},
                "metadata": {
                    "provider": "oanda",
                    "provider_symbol": _provider_symbol(source),
                    "price_component": price_component,
                },
            }
        )
    return _frame_from_records(records)


def fetch_dukascopy_node_candles(
    source: LiveSource,
    since: datetime,
    until: datetime,
    now: datetime | None = None,
) -> pd.DataFrame:
    command = _require_command("npx")
    start = _utc_dt(since)
    end = _utc_dt(until)
    from_day = start.date().isoformat()
    to_day = end.date().isoformat()
    if end.time() != datetime.min.time():
        to_day = (end + timedelta(days=1)).date().isoformat()
    with tempfile.TemporaryDirectory(prefix="ict-duka-") as tmp:
        tmp_path = Path(tmp)
        args = [
            command,
            "--yes",
            "dukascopy-node",
            "-i",
            _provider_symbol(source),
            "-from",
            from_day,
            "-to",
            to_day,
            "-t",
            "m1",
            "-f",
            "csv",
            "-r",
            "3",
            "-rp",
            "1500",
            "-bs",
            "2",
            "-bp",
            "1500",
        ]
        completed = subprocess.run(args, cwd=tmp_path, text=True, capture_output=True)
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "").strip()
            raise LiveProviderError(_short_error(message or "dukascopy-node failed."))
        csv_files = sorted(tmp_path.glob("*.csv"))
        if not csv_files:
            raise LiveProviderError(f"dukascopy-node did not create a CSV for {_provider_symbol(source)}.")
        rows = pd.concat((pd.read_csv(path) for path in csv_files), ignore_index=True)
    return normalize_dukascopy_node_rows(source, rows, since, until, now=now)


def normalize_dukascopy_node_rows(
    source: LiveSource,
    rows: pd.DataFrame,
    since: datetime,
    until: datetime,
    now: datetime | None = None,
) -> pd.DataFrame:
    if rows.empty:
        return _frame_from_records([])
    current = _utc_dt(now or datetime.now(timezone.utc))
    start = _utc_dt(since)
    end = _utc_dt(until)
    normalized = rows.rename(columns={column: str(column).strip().lower() for column in rows.columns}).copy()
    required = {"timestamp", "open", "high", "low", "close"}
    missing = sorted(required - set(normalized.columns))
    if missing:
        raise LiveProviderError(f"dukascopy-node CSV is missing columns: {missing}")
    timestamps = pd.to_datetime(normalized["timestamp"], errors="coerce", unit="ms", utc=True)
    records = []
    for position, row in enumerate(normalized.to_dict(orient="records")):
        if pd.isna(timestamps.iloc[position]):
            continue
        time_open = timestamps.iloc[position].to_pydatetime()
        if not (start <= time_open < end) or time_open + M1 > current:
            continue
        volume = _optional_float(row.get("volume"))
        records.append(
            {
                "symbol_code": source.symbol_code,
                "source_name": source.source_name,
                "source_symbol": source.source_symbol,
                "timeframe": source.timeframe,
                "time_open": time_open,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "tick_volume": volume,
                "real_volume": volume,
                "spread": None,
                "quality_flags": {},
                "metadata": {"provider": "dukascopy_node", "provider_symbol": _provider_symbol(source)},
            }
        )
    return _frame_from_records(records)


def fetch_databento_candles(
    source: LiveSource,
    since: datetime,
    until: datetime,
    now: datetime | None = None,
) -> pd.DataFrame:
    provider = DatabentoHistoricalProvider()
    raw = provider.fetch_candles(
        _provider_symbol(source),
        source.timeframe,
        _utc_dt(since),
        _utc_dt(until),
        dataset=source.dataset or "GLBX.MDP3",
        schema=source.schema,
        max_cost_usd=source.max_cost_usd if source.max_cost_usd is not None else 1.0,
    )
    return normalize_databento_rows(source, raw, since, until, now=now)


def normalize_databento_rows(
    source: LiveSource,
    rows: pd.DataFrame,
    since: datetime,
    until: datetime,
    now: datetime | None = None,
) -> pd.DataFrame:
    if rows.empty:
        return _frame_from_records([])
    current = _utc_dt(now or datetime.now(timezone.utc))
    start = _utc_dt(since)
    end = _utc_dt(until)
    records = []
    for row in rows.to_dict(orient="records"):
        time_open = pd.Timestamp(row["time"]).to_pydatetime()
        time_open = _utc_dt(time_open)
        if not (start <= time_open < end) or time_open + M1 > current:
            continue
        records.append(
            {
                "symbol_code": source.symbol_code,
                "source_name": source.source_name,
                "source_symbol": source.source_symbol,
                "timeframe": source.timeframe,
                "time_open": time_open,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "tick_volume": _optional_float(row.get("tick_volume")),
                "real_volume": _optional_float(row.get("real_volume")),
                "spread": None,
                "quality_flags": {},
                "metadata": {
                    "provider": "databento",
                    "provider_symbol": _provider_symbol(source),
                    "dataset": source.dataset or "GLBX.MDP3",
                    "schema": source.schema or "ohlcv-1m",
                },
            }
        )
    return _frame_from_records(records)


def discover_oanda_instruments(
    account_id: str | None = None,
    token: str | None = None,
    base_url: str | None = None,
) -> list[dict]:
    account_id = account_id or _oanda_account_id()
    token = token or _oanda_api_token()
    base_url = (base_url or _oanda_api_url()).rstrip("/")
    payload = _http_json(
        f"{base_url}/v3/accounts/{account_id}/instruments",
        headers={"Authorization": f"Bearer {token}"},
    )
    instruments = payload.get("instruments") if isinstance(payload, dict) else None
    if not isinstance(instruments, list):
        raise LiveProviderError("OANDA instrument discovery returned no instruments list.")
    return instruments


def _http_json(url: str, headers: dict[str, str] | None = None):
    request_headers = {"Accept": "application/json", "User-Agent": "ict-live-collector/0.2"}
    request_headers.update(headers or {})
    request = Request(url, headers=request_headers)
    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""
        raise LiveProviderError(f"HTTP {exc.code} for {url}: {_short_error(body)}") from exc
    except URLError as exc:
        raise LiveProviderError(f"Network error for {url}: {exc}") from exc


def _frame_from_records(records: list[dict]) -> pd.DataFrame:
    columns = [
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
    if not records:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame.from_records(records, columns=columns)
    frame["time_open"] = pd.to_datetime(frame["time_open"], utc=True)
    return frame.drop_duplicates(subset=["time_open"], keep="last").sort_values("time_open").reset_index(drop=True)


def _provider_symbol(source: LiveSource) -> str:
    return source.provider_symbol or source.source_symbol


def _require_command(name: str) -> str:
    command = shutil.which(name) or shutil.which(f"{name}.cmd")
    if command:
        return command
    raise LiveProviderError(f"Required command '{name}' is not available in the collector runtime.")


def _optional_float(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _oanda_api_url() -> str:
    settings = get_settings()
    if settings.oanda_api_url:
        return settings.oanda_api_url
    return OANDA_LIVE_URL if settings.oanda_env.lower() == "live" else OANDA_PRACTICE_URL


def _oanda_api_token() -> str:
    token = get_settings().oanda_api_token
    if not token:
        raise LiveProviderError("Set OANDA_API_TOKEN before collecting OANDA candles.")
    return token


def _oanda_account_id() -> str:
    account_id = get_settings().oanda_account_id
    if not account_id:
        raise LiveProviderError("Set OANDA_ACCOUNT_ID before discovering OANDA instruments.")
    return account_id


def _oanda_price_bucket(row: dict, price_component: str) -> dict:
    if price_component == "M" and isinstance(row.get("mid"), dict):
        return row["mid"]
    if price_component == "B" and isinstance(row.get("bid"), dict):
        return row["bid"]
    if price_component == "A" and isinstance(row.get("ask"), dict):
        return row["ask"]
    if isinstance(row.get("mid"), dict):
        return row["mid"]
    if isinstance(row.get("bid"), dict) and isinstance(row.get("ask"), dict):
        bid = row["bid"]
        ask = row["ask"]
        return {
            key: (float(bid[key]) + float(ask[key])) / 2
            for key in ("o", "h", "l", "c")
            if key in bid and key in ask
        }
    raise LiveProviderError("OANDA candle has no usable price bucket.")


def _parse_oanda_time(value: str) -> datetime:
    text = value.strip()
    if not text:
        raise LiveProviderError("OANDA candle is missing a time value.")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, tail = text.split(".", 1)
        sign_indexes = [index for index in (tail.find("+"), tail.find("-")) if index >= 0]
        if sign_indexes:
            sign_index = min(sign_indexes)
            fraction = tail[:sign_index]
            suffix = tail[sign_index:]
        else:
            fraction = tail
            suffix = ""
        text = f"{head}.{fraction[:6].ljust(6, '0')}{suffix}"
    return _utc_dt(datetime.fromisoformat(text))


def _utc_dt(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _short_error(exc: Exception | str, limit: int = 420) -> str:
    message = str(exc).replace("\n", " ")
    return message if len(message) <= limit else message[: limit - 3] + "..."
