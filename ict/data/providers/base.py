from __future__ import annotations

from datetime import datetime
from typing import Protocol

import pandas as pd


class MarketDataProvider(Protocol):
    source_name: str

    def list_symbols(self) -> list[dict]:
        ...

    def fetch_candles(
        self,
        source_symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        **kwargs,
    ) -> pd.DataFrame:
        ...
