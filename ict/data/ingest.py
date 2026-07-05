from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from ict.data.normalizer import TransformContext, transformer_for_source
from ict.data.providers import CSVProvider, DukascopyCSVProvider
from ict.data.quality import analyze_candle_quality, annotate_candle_quality, prepare_candles_for_storage
from ict.db.repositories import AliasRepository, CandleRepository, ImportJobRepository
from ict.db.session import session_scope


def provider_for_source(source_type: str):
    if source_type == "mt5":
        from ict.data.providers.mt5_provider import MT5Provider

        return MT5Provider()
    if source_type == "dukascopy":
        return DukascopyCSVProvider()
    if source_type == "file":
        return CSVProvider()
    raise ValueError(f"Unsupported source_type: {source_type}")


def ingest_market_data(
    symbol_code: str,
    source_name: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    **provider_kwargs: Any,
) -> dict[str, Any]:
    with session_scope() as session:
        alias = AliasRepository(session).resolve(symbol_code, source_name)
        source = alias.source
        symbol = alias.symbol
        provider = provider_for_source(source.source_type)
        transformer = transformer_for_source(source.source_type)
        job = ImportJobRepository(session).create(
            source.id,
            symbol.id,
            alias.id,
            timeframe,
            start,
            end,
            source_params=provider_kwargs,
        )
        job.status = "running"
        raw = provider.fetch_candles(alias.source_symbol, timeframe, start, end, **provider_kwargs)
        job.rows_fetched = int(len(raw))
        mapping = provider_kwargs.get("mapping") or {}
        context = TransformContext(
            source_name=source.name,
            symbol_code=symbol.symbol_code,
            source_symbol=alias.source_symbol,
            timeframe=timeframe.upper(),
            source_timezone=provider_kwargs.get("timezone") or alias.source_timezone or source.base_timezone or "UTC",
            price_multiplier=float(alias.price_multiplier or 1),
            column_mapping=mapping,
        )
        normalized = annotate_candle_quality(transformer.transform(raw, context), timeframe)
        quality = analyze_candle_quality(normalized, timeframe)
        storage_frame = prepare_candles_for_storage(normalized)
        candles = CandleRepository(session)
        rows = candles.rows_for_frame(
            symbol.id,
            source.id,
            alias.source_symbol,
            timeframe,
            storage_frame,
        )
        existing = candles.count_existing_candles(
            symbol.id,
            source.id,
            timeframe,
            [row["time_open"] for row in rows],
        )
        written = candles.upsert_candles(rows)
        inserted = max(0, len(rows) - existing)
        updated = min(existing, len(rows))
        skipped = max(0, len(normalized) - len(storage_frame))
        job.rows_inserted = inserted
        job.rows_updated = updated
        job.rows_skipped = skipped
        job.quality_report = quality.as_dict()
        job.status = "completed"
        return {
            "job_id": job.id,
            "rows_fetched": job.rows_fetched,
            "rows_inserted": inserted,
            "rows_updated": updated,
            "rows_skipped": skipped,
            "rows_written": written,
            "quality_report": quality.as_dict(),
        }


def load_csv_mapping(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    import yaml

    with open(path, "r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return {key: value for key, value in payload.items() if key != "timezone"}


def load_csv_timezone(path: str | None) -> str | None:
    if not path:
        return None
    import yaml

    with open(path, "r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    return payload.get("timezone")
