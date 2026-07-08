from __future__ import annotations

import io
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ict.core.config import get_settings


TIMEFRAME_TO_SCHEMA = {
    "M1": "ohlcv-1m",
    "H1": "ohlcv-1h",
    "D1": "ohlcv-1d",
}

FIXED_PRICE_SCALE = 1e-9


class DatabentoHistoricalProvider:
    """Adapter for Databento historical OHLCV data and downloaded CSV exports."""

    source_name = "databento"

    def __init__(self, client: Any | None = None):
        self._client = client

    def list_symbols(self) -> list[dict]:
        return []

    def fetch_candles(
        self,
        source_symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        **kwargs,
    ) -> pd.DataFrame:
        file_path = kwargs.get("file_path")
        if file_path:
            raw = self._read_export(Path(file_path), kwargs.get("schema") or self._schema(timeframe))
        else:
            cost = self.estimate_cost(source_symbol, timeframe, start, end, **kwargs)
            max_cost = float(kwargs.get("max_cost_usd", 5.0))
            if cost > max_cost:
                raise ValueError(
                    f"Databento request cost ${cost:.4f}, above configured max_cost_usd=${max_cost:.4f}."
                )
            store = self._historical().timeseries.get_range(
                dataset=kwargs.get("dataset", "GLBX.MDP3"),
                symbols=[source_symbol],
                schema=kwargs.get("schema") or self._schema(timeframe),
                stype_in=kwargs.get("stype_in") or self._infer_stype_in(source_symbol),
                start=start,
                end=end,
            )
            raw = store.to_df()

        return self._to_provider_frame(raw, start, end)

    def estimate_cost(
        self,
        source_symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        **kwargs,
    ) -> float:
        return float(
            self._historical().metadata.get_cost(
                dataset=kwargs.get("dataset", "GLBX.MDP3"),
                symbols=[source_symbol],
                schema=kwargs.get("schema") or self._schema(timeframe),
                stype_in=kwargs.get("stype_in") or self._infer_stype_in(source_symbol),
                start=start,
                end=end,
                limit=kwargs.get("limit"),
            )
        )

    def _historical(self):
        if self._client is not None:
            return self._client
        try:
            import databento as db
        except ImportError as exc:  # pragma: no cover - exercised in incomplete installs
            raise RuntimeError("Install the 'databento' package to use the Databento provider.") from exc

        api_key = get_settings().databento_api_key
        if not api_key:
            raise ValueError("DATABENTO_API_KEY is required for Databento API requests.")
        self._client = db.Historical(api_key)
        return self._client

    def _read_export(self, path: Path, schema: str) -> pd.DataFrame:
        if not path.exists():
            raise FileNotFoundError(path)
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
                schema_names = [name for name in csv_names if schema.lower() in name.lower()]
                if not schema_names:
                    raise ValueError(f"Databento archive does not contain a {schema} CSV file.")
                with archive.open(schema_names[0]) as handle:
                    return pd.read_csv(handle)
        return pd.read_csv(path)

    def _to_provider_frame(self, raw: pd.DataFrame, start: datetime, end: datetime) -> pd.DataFrame:
        frame = raw.reset_index() if raw.index.name else raw.copy()
        required = ["open", "high", "low", "close", "volume"]
        missing = [column for column in required if column not in frame.columns]
        if missing:
            raise ValueError(f"Databento OHLCV data missing columns: {missing}")

        time_column = self._time_column(frame)
        out = pd.DataFrame()
        out["time"] = pd.to_datetime(frame[time_column], utc=True)
        for column in ["open", "high", "low", "close"]:
            out[column] = self._normalize_price(frame[column])
        out["tick_volume"] = pd.to_numeric(frame["volume"], errors="coerce")
        out["real_volume"] = out["tick_volume"]

        passthrough = [column for column in frame.columns if column not in {*required, time_column}]
        for column in passthrough:
            out[column] = frame[column]

        start_ts = pd.Timestamp(start).tz_convert("UTC") if pd.Timestamp(start).tzinfo else pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end).tz_convert("UTC") if pd.Timestamp(end).tzinfo else pd.Timestamp(end, tz="UTC")
        return out[(out["time"] >= start_ts) & (out["time"] <= end_ts)].sort_values("time").reset_index(drop=True)

    def _time_column(self, frame: pd.DataFrame) -> str:
        for column in ["ts_event", "time", "timestamp"]:
            if column in frame.columns:
                return column
        raise ValueError("Databento OHLCV data missing a timestamp column.")

    def _normalize_price(self, values: pd.Series) -> pd.Series:
        numeric = pd.to_numeric(values, errors="raise")
        non_null = numeric.dropna().abs()
        if not non_null.empty and non_null.median() > 1_000_000:
            return numeric * FIXED_PRICE_SCALE
        return numeric

    def _schema(self, timeframe: str) -> str:
        upper = timeframe.upper()
        if upper in TIMEFRAME_TO_SCHEMA:
            return TIMEFRAME_TO_SCHEMA[upper]
        if timeframe in set(TIMEFRAME_TO_SCHEMA.values()):
            return timeframe
        raise ValueError(f"Unsupported Databento timeframe: {timeframe}")

    def _infer_stype_in(self, source_symbol: str) -> str:
        if ".c." in source_symbol:
            return "continuous"
        if source_symbol.endswith(".FUT"):
            return "parent"
        return "raw_symbol"
