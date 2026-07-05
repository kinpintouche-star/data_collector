from __future__ import annotations

from datetime import datetime
from typing import Any

from ict.data.ingest import ingest_market_data


def ingest_symbol(
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    **kwargs: Any,
) -> int:
    """Backward-compatible wrapper around generic ingestion for MT5."""

    result = ingest_market_data(symbol, "mt5", timeframe, start, end, **kwargs)
    return int(result["rows_inserted"])
