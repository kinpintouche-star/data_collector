from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pandas as pd


BINANCE_KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
    "ignore",
]


TIMEFRAME_TO_INTERVAL = {
    "M1": "1m",
    "M3": "3m",
    "M5": "5m",
    "M15": "15m",
    "M30": "30m",
    "H1": "1h",
    "H2": "2h",
    "H4": "4h",
    "H6": "6h",
    "H8": "8h",
    "H12": "12h",
    "D1": "1d",
}


@dataclass(frozen=True)
class BinanceArchive:
    frequency: str
    label: str


class BinancePublicDataProvider:
    """Adapter for Binance public ZIP archives from data.binance.vision."""

    source_name = "binance"

    def __init__(self, base_url: str = "https://data.binance.vision"):
        self.base_url = base_url.rstrip("/")

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
            frame = self._read_path(Path(file_path))
        else:
            interval = self._interval(timeframe)
            frequency = kwargs.get("frequency", "monthly")
            market = self._market_path(kwargs.get("market", "spot"))
            missing_policy = kwargs.get("missing_policy", "raise")
            frames = []
            for archive in self._archives(start, end, frequency):
                url = self._archive_url(market, archive.frequency, source_symbol, interval, archive.label)
                try:
                    frames.append(self._read_zip_bytes(self._download(url)))
                except HTTPError as exc:
                    if exc.code == 404 and missing_policy == "skip":
                        continue
                    raise
            frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=BINANCE_KLINE_COLUMNS)

        return self._to_provider_frame(frame, start, end)

    def _archive_url(self, market: str, frequency: str, symbol: str, interval: str, label: str) -> str:
        filename = f"{symbol}-{interval}-{label}.zip"
        return f"{self.base_url}/data/{market}/{frequency}/klines/{symbol}/{interval}/{filename}"

    def _download(self, url: str) -> bytes:
        request = Request(url, headers={"User-Agent": "ict-crt-platform/0.1"})
        with urlopen(request, timeout=60) as response:
            return response.read()

    def _read_path(self, path: Path) -> pd.DataFrame:
        if not path.exists():
            raise FileNotFoundError(path)
        if path.suffix.lower() == ".zip":
            return self._read_zip_bytes(path.read_bytes())
        return self._read_csv_buffer(path)

    def _read_zip_bytes(self, payload: bytes) -> pd.DataFrame:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if not csv_names:
                raise ValueError("Binance archive does not contain a CSV file.")
            with archive.open(csv_names[0]) as handle:
                return self._read_csv_buffer(handle)

    def _read_csv_buffer(self, buffer) -> pd.DataFrame:
        frame = pd.read_csv(buffer, header=None)
        if frame.empty:
            return pd.DataFrame(columns=BINANCE_KLINE_COLUMNS)
        if not str(frame.iloc[0, 0]).strip().isdigit():
            frame = frame.iloc[1:].reset_index(drop=True)
        frame = frame.iloc[:, : len(BINANCE_KLINE_COLUMNS)]
        frame.columns = BINANCE_KLINE_COLUMNS[: len(frame.columns)]
        return frame

    def _to_provider_frame(self, frame: pd.DataFrame, start: datetime, end: datetime) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame(
                columns=[
                    "time",
                    "open",
                    "high",
                    "low",
                    "close",
                    "tick_volume",
                    "real_volume",
                    "close_time",
                    "quote_asset_volume",
                    "taker_buy_base_asset_volume",
                    "taker_buy_quote_asset_volume",
                ]
            )
        out = frame.copy()
        out["time"] = self._parse_epoch(out["open_time"])
        out["close_time"] = self._parse_epoch(out["close_time"])
        for column in [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_asset_volume",
            "number_of_trades",
            "taker_buy_base_asset_volume",
            "taker_buy_quote_asset_volume",
        ]:
            out[column] = pd.to_numeric(out[column], errors="coerce")
        out = out[(out["time"] >= pd.Timestamp(start)) & (out["time"] <= pd.Timestamp(end))]
        return (
            out.rename(columns={"number_of_trades": "tick_volume", "volume": "real_volume"})
            .sort_values("time")
            .reset_index(drop=True)
        )

    def _parse_epoch(self, values: pd.Series) -> pd.Series:
        numeric = pd.to_numeric(values, errors="raise")
        clean = numeric.dropna().astype("int64").abs()
        unit = "us" if not clean.empty and clean.max() >= 10**15 else "ms"
        return pd.to_datetime(numeric, unit=unit, utc=True)

    def _interval(self, timeframe: str) -> str:
        upper = timeframe.upper()
        if upper in TIMEFRAME_TO_INTERVAL:
            return TIMEFRAME_TO_INTERVAL[upper]
        if timeframe in set(TIMEFRAME_TO_INTERVAL.values()):
            return timeframe
        raise ValueError(f"Unsupported Binance timeframe: {timeframe}")

    def _market_path(self, market: str) -> str:
        normalized = market.lower().replace("_", "/")
        aliases = {
            "spot": "spot",
            "um": "futures/um",
            "futures/um": "futures/um",
            "usd-m": "futures/um",
            "cm": "futures/cm",
            "futures/cm": "futures/cm",
            "coin-m": "futures/cm",
        }
        if normalized not in aliases:
            raise ValueError(f"Unsupported Binance market: {market}")
        return aliases[normalized]

    def _archives(self, start: datetime, end: datetime, frequency: str) -> list[BinanceArchive]:
        if frequency == "daily":
            days = pd.date_range(pd.Timestamp(start).date(), pd.Timestamp(end).date(), freq="D")
            return [BinanceArchive("daily", day.strftime("%Y-%m-%d")) for day in days]
        if frequency == "monthly":
            start_month = pd.Period(pd.Timestamp(start).strftime("%Y-%m"), freq="M")
            end_month = pd.Period(pd.Timestamp(end).strftime("%Y-%m"), freq="M")
            months = pd.period_range(start_month, end_month, freq="M")
            return [BinanceArchive("monthly", month.strftime("%Y-%m")) for month in months]
        raise ValueError("Binance frequency must be 'daily' or 'monthly'.")
