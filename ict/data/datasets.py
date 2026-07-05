from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ict.data.quality import analyze_candle_quality
from ict.db.repositories import AliasRepository, CandleRepository, DatasetRepository
from ict.db.session import session_scope


def create_dataset(
    symbol_code: str,
    source_name: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    name: str | None = None,
    version: str = "1",
) -> dict[str, Any]:
    with session_scope() as session:
        return create_dataset_in_session(session, symbol_code, source_name, timeframe, start, end, name, version)


def create_dataset_in_session(
    session: Session,
    symbol_code: str,
    source_name: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    name: str | None = None,
    version: str = "1",
) -> dict[str, Any]:
    alias = AliasRepository(session).resolve(symbol_code, source_name)
    frame = CandleRepository(session).load_candles(
        alias.symbol_id,
        alias.source_id,
        timeframe,
        start,
        end,
    )
    report = analyze_candle_quality(frame, timeframe)
    quality_report = report.as_dict()
    stats = {**quality_report, "metadata": {"quality_report": quality_report}}
    dataset_id = DatasetRepository(session).upsert_dataset(
        alias.symbol_id,
        alias.source_id,
        timeframe,
        start,
        end,
        name,
        stats,
        dataset_version=version,
    )
    return {"dataset_id": dataset_id, **stats}
