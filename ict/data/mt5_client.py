from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from ict.core.config import Settings, get_settings
from ict.core.timezones import ensure_utc


TIMEFRAME_MAP = {
    "M1": "TIMEFRAME_M1",
    "M15": "TIMEFRAME_M15",
    "H1": "TIMEFRAME_H1",
    "D1": "TIMEFRAME_D1",
}


class MT5UnavailableError(RuntimeError):
    pass


def _import_mt5() -> Any:
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError as exc:
        raise MT5UnavailableError(
            "MetaTrader5 is not installed. Install it on the Windows host that runs the MT5 terminal."
        ) from exc
    return mt5


@dataclass
class MT5Client:
    settings: Settings | None = None

    def __post_init__(self) -> None:
        self.settings = self.settings or get_settings()
        self.mt5 = _import_mt5()

    def initialize(self) -> bool:
        kwargs: dict[str, Any] = {}
        if self.settings and self.settings.mt5_path:
            kwargs["path"] = self.settings.mt5_path
        if self.settings and self.settings.mt5_login:
            kwargs["login"] = self.settings.mt5_login
        if self.settings and self.settings.mt5_password:
            kwargs["password"] = self.settings.mt5_password
        if self.settings and self.settings.mt5_server:
            kwargs["server"] = self.settings.mt5_server
        ok = self.mt5.initialize(**kwargs)
        if not ok:
            raise MT5UnavailableError(f"MT5 initialize failed: {self.mt5.last_error()}")
        return True

    def shutdown(self) -> None:
        self.mt5.shutdown()

    def terminal_info(self) -> Any:
        return self.mt5.terminal_info()

    def account_info(self) -> Any:
        return self.mt5.account_info()

    def symbol_select(self, symbol: str) -> bool:
        if not self.mt5.symbol_select(symbol, True):
            raise ValueError(f"Unable to select MT5 symbol {symbol!r}: {self.mt5.last_error()}")
        return True

    def symbol_info(self, symbol: str) -> Any:
        info = self.mt5.symbol_info(symbol)
        if info is None:
            raise ValueError(f"MT5 symbol not found: {symbol!r}")
        return info

    def copy_rates_range(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        timeframe_attr = TIMEFRAME_MAP.get(timeframe.upper())
        if timeframe_attr is None:
            raise ValueError(f"Unsupported MT5 timeframe: {timeframe}")
        self.symbol_select(symbol)
        rates = self.mt5.copy_rates_range(
            symbol,
            getattr(self.mt5, timeframe_attr),
            ensure_utc(start),
            ensure_utc(end),
        )
        if rates is None:
            raise RuntimeError(f"copy_rates_range failed for {symbol}: {self.mt5.last_error()}")
        df = pd.DataFrame(rates)
        if df.empty:
            return pd.DataFrame(
                columns=["time_open", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]
            )
        df["time_open"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df.drop(columns=["time"]).rename(columns={"real_volume": "real_volume"})
