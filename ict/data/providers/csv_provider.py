from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd


class CSVProvider:
    source_name = "csv"

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
        if not file_path:
            raise ValueError("CSVProvider requires file_path.")
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(path)
        if path.suffix.lower() in {".tsv", ".txt"}:
            return pd.read_csv(path, sep="\t")
        return pd.read_csv(path)
