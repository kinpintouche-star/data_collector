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

        model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

else:

    @dataclass(frozen=True)
    class Settings:
        database_url: str = os.getenv("DATABASE_URL", "postgresql+psycopg://ict:ict@localhost:5432/ict")
        mt5_path: Optional[str] = os.getenv("MT5_PATH")
        mt5_login: Optional[int] = int(os.environ["MT5_LOGIN"]) if os.getenv("MT5_LOGIN") else None
        mt5_password: Optional[str] = os.getenv("MT5_PASSWORD")
        mt5_server: Optional[str] = os.getenv("MT5_SERVER")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
