from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from ict.data.providers.base import MarketDataProvider


class DummyProvider:
    source_name = "dummy"

    def list_symbols(self) -> list[dict]:
        return [{"source_symbol": "DUMMY"}]

    def fetch_candles(self, source_symbol: str, timeframe: str, start: datetime, end: datetime, **kwargs) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "time": [start],
                "open": [1],
                "high": [2],
                "low": [0],
                "close": [1.5],
            }
        )


def test_data_provider_interface() -> None:
    provider: MarketDataProvider = DummyProvider()
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    frame = provider.fetch_candles("DUMMY", "M1", start, start)

    assert provider.source_name == "dummy"
    assert provider.list_symbols()[0]["source_symbol"] == "DUMMY"
    assert len(frame) == 1
