from __future__ import annotations

from datetime import datetime

import pandas as pd

from ict.data.mt5_client import MT5Client


class MT5Provider:
    source_name = "mt5"

    def __init__(self, client: MT5Client | None = None):
        self.client = client or MT5Client()

    def list_symbols(self) -> list[dict]:
        self.client.initialize()
        try:
            symbols = self.client.mt5.symbols_get()
            return [
                {
                    "source_symbol": symbol.name,
                    "description": getattr(symbol, "description", None),
                    "path": getattr(symbol, "path", None),
                }
                for symbol in symbols or []
            ]
        finally:
            self.client.shutdown()

    def fetch_candles(
        self,
        source_symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        **kwargs,
    ) -> pd.DataFrame:
        self.client.initialize()
        try:
            return self.client.copy_rates_range(source_symbol, timeframe, start, end)
        finally:
            self.client.shutdown()
