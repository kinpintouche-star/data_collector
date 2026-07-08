from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

try:
    from pydantic import Field
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError:  # pragma: no cover - exercised only in partial local envs
    BaseSettings = None
    Field = None
    SettingsConfigDict = None


if BaseSettings is not None:

    class Settings(BaseSettings):  # type: ignore[misc]
        database_url: str = Field(  # type: ignore[misc]
            default="postgresql+psycopg://ict:ict@localhost:5432/ict",
            alias="DATABASE_URL",
        )
        mt5_path: Optional[str] = Field(default=None, alias="MT5_PATH")  # type: ignore[misc]
        mt5_login: Optional[int] = Field(default=None, alias="MT5_LOGIN")  # type: ignore[misc]
        mt5_password: Optional[str] = Field(default=None, alias="MT5_PASSWORD")  # type: ignore[misc]
        mt5_server: Optional[str] = Field(default=None, alias="MT5_SERVER")  # type: ignore[misc]
        databento_api_key: Optional[str] = Field(default=None, alias="DATABENTO_API_KEY")  # type: ignore[misc]
        live_remote_database_url: Optional[str] = Field(default=None, alias="LIVE_REMOTE_DATABASE_URL")  # type: ignore[misc]
        market_archive_key: Optional[str] = Field(default=None, alias="MARKET_ARCHIVE_KEY")  # type: ignore[misc]
        market_archive_cache_dir: str = Field(default=".cache/market_archive", alias="MARKET_ARCHIVE_CACHE_DIR")  # type: ignore[misc]
        market_archive_max_bucket_gb: float = Field(default=10.0, alias="MARKET_ARCHIVE_MAX_BUCKET_GB")  # type: ignore[misc]
        market_archive_prefix: str = Field(default="market-candles", alias="MARKET_ARCHIVE_PREFIX")  # type: ignore[misc]
        r2_account_id: Optional[str] = Field(default=None, alias="R2_ACCOUNT_ID")  # type: ignore[misc]
        r2_access_key_id: Optional[str] = Field(default=None, alias="R2_ACCESS_KEY_ID")  # type: ignore[misc]
        r2_secret_access_key: Optional[str] = Field(default=None, alias="R2_SECRET_ACCESS_KEY")  # type: ignore[misc]
        r2_bucket: Optional[str] = Field(default=None, alias="R2_BUCKET")  # type: ignore[misc]
        r2_endpoint_url: Optional[str] = Field(default=None, alias="R2_ENDPOINT_URL")  # type: ignore[misc]
        oanda_api_token: Optional[str] = Field(default=None, alias="OANDA_API_TOKEN")  # type: ignore[misc]
        oanda_account_id: Optional[str] = Field(default=None, alias="OANDA_ACCOUNT_ID")  # type: ignore[misc]
        oanda_env: str = Field(default="practice", alias="OANDA_ENV")  # type: ignore[misc]
        oanda_api_url: Optional[str] = Field(default=None, alias="OANDA_API_URL")  # type: ignore[misc]

        model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

else:

    @dataclass(frozen=True)
    class Settings:
        database_url: str = os.getenv("DATABASE_URL", "postgresql+psycopg://ict:ict@localhost:5432/ict")
        mt5_path: Optional[str] = os.getenv("MT5_PATH")
        mt5_login: Optional[int] = int(os.environ["MT5_LOGIN"]) if os.getenv("MT5_LOGIN") else None
        mt5_password: Optional[str] = os.getenv("MT5_PASSWORD")
        mt5_server: Optional[str] = os.getenv("MT5_SERVER")
        databento_api_key: Optional[str] = os.getenv("DATABENTO_API_KEY")
        live_remote_database_url: Optional[str] = os.getenv("LIVE_REMOTE_DATABASE_URL")
        market_archive_key: Optional[str] = os.getenv("MARKET_ARCHIVE_KEY")
        market_archive_cache_dir: str = os.getenv("MARKET_ARCHIVE_CACHE_DIR", ".cache/market_archive")
        market_archive_max_bucket_gb: float = float(os.getenv("MARKET_ARCHIVE_MAX_BUCKET_GB", "10"))
        market_archive_prefix: str = os.getenv("MARKET_ARCHIVE_PREFIX", "market-candles")
        r2_account_id: Optional[str] = os.getenv("R2_ACCOUNT_ID")
        r2_access_key_id: Optional[str] = os.getenv("R2_ACCESS_KEY_ID")
        r2_secret_access_key: Optional[str] = os.getenv("R2_SECRET_ACCESS_KEY")
        r2_bucket: Optional[str] = os.getenv("R2_BUCKET")
        r2_endpoint_url: Optional[str] = os.getenv("R2_ENDPOINT_URL")
        oanda_api_token: Optional[str] = os.getenv("OANDA_API_TOKEN")
        oanda_account_id: Optional[str] = os.getenv("OANDA_ACCOUNT_ID")
        oanda_env: str = os.getenv("OANDA_ENV", "practice")
        oanda_api_url: Optional[str] = os.getenv("OANDA_API_URL")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
