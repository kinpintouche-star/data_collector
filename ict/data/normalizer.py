from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


CANONICAL_COLUMNS = [
    "time_open",
    "open",
    "high",
    "low",
    "close",
    "tick_volume",
    "real_volume",
    "spread",
    "source_metadata",
]


@dataclass(frozen=True)
class TransformContext:
    source_name: str
    symbol_code: str
    source_symbol: str
    timeframe: str
    source_timezone: str = "UTC"
    price_multiplier: float = 1.0
    column_mapping: dict[str, str] = field(default_factory=dict)


class CandleTransformer:
    def transform(self, raw: pd.DataFrame, context: TransformContext) -> pd.DataFrame:
        raise NotImplementedError


class ColumnMappingTransformer(CandleTransformer):
    """Normalizes arbitrary source columns into one OHLCV candle shape."""

    default_mapping = {
        "time": "time",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "tick_volume": "tick_volume",
        "real_volume": "real_volume",
        "spread": "spread",
    }

    def transform(self, raw: pd.DataFrame, context: TransformContext) -> pd.DataFrame:
        if raw.empty:
            return pd.DataFrame(columns=CANONICAL_COLUMNS)

        mapping = {**self.default_mapping, **context.column_mapping}
        missing = [target for target in ["time", "open", "high", "low", "close"] if mapping.get(target) not in raw]
        if missing:
            missing_sources = {target: mapping.get(target) for target in missing}
            raise ValueError(f"Missing source columns for canonical fields: {missing_sources}")

        out = pd.DataFrame()
        source_time_column = mapping["time"]
        parsed_time = pd.to_datetime(raw[source_time_column], errors="raise")
        if parsed_time.dt.tz is None:
            parsed_time = parsed_time.dt.tz_localize(ZoneInfo(context.source_timezone))
        out["time_open"] = parsed_time.dt.tz_convert("UTC")

        for column in ["open", "high", "low", "close"]:
            out[column] = pd.to_numeric(raw[mapping[column]], errors="raise") * context.price_multiplier

        for column in ["tick_volume", "real_volume", "spread"]:
            source_column = mapping.get(column)
            if source_column and source_column in raw:
                out[column] = pd.to_numeric(raw[source_column], errors="coerce")
            else:
                out[column] = pd.NA

        passthrough_columns = [column for column in raw.columns if column not in set(mapping.values())]
        out["source_metadata"] = [
            {
                "source_name": context.source_name,
                "source_symbol": context.source_symbol,
                "symbol_code": context.symbol_code,
                "extra": {column: _json_safe(row[column]) for column in passthrough_columns},
            }
            for _, row in raw.iterrows()
        ]
        return normalize_transformed_candles(out)


class MT5Transformer(ColumnMappingTransformer):
    default_mapping = {
        "time": "time_open",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "tick_volume": "tick_volume",
        "real_volume": "real_volume",
        "spread": "spread",
    }


class DukascopyCSVTransformer(ColumnMappingTransformer):
    default_mapping = {
        "time": "Gmt time",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "tick_volume": "Volume",
        "real_volume": "Volume",
        "spread": "spread",
    }


def normalize_transformed_candles(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["time_open"] = pd.to_datetime(out["time_open"], utc=True)
    out = out.sort_values("time_open")
    for column in ["open", "high", "low", "close"]:
        out[column] = pd.to_numeric(out[column], errors="raise")
    for column in ["tick_volume", "real_volume"]:
        out[column] = pd.to_numeric(out[column], errors="coerce").astype("Float64")
    out["spread"] = pd.to_numeric(out["spread"], errors="coerce").astype("Float64")
    if "source_metadata" not in out:
        out["source_metadata"] = [{} for _ in range(len(out))]
    return out[CANONICAL_COLUMNS].reset_index(drop=True)


def transformer_for_source(source_type: str) -> CandleTransformer:
    if source_type == "mt5":
        return MT5Transformer()
    if source_type == "dukascopy":
        return DukascopyCSVTransformer()
    return ColumnMappingTransformer()


def _json_safe(value: Any) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value
