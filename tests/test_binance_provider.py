from __future__ import annotations

import zipfile
from datetime import datetime, timezone

import pandas as pd

from ict.data.providers.binance_provider import BinancePublicDataProvider


def test_binance_provider_reads_zipped_klines_with_microsecond_timestamps(tmp_path) -> None:
    zip_path = tmp_path / "BTCUSDT-1m-2026-01.zip"
    rows = "\n".join(
        [
            "1767225600000000,42000,42100,41900,42050,12.5,1767225659999999,525000,120,6,252000,0",
            "1767225660000000,42050,42200,42000,42150,10.0,1767225719999999,421500,100,4,168600,0",
        ]
    )
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("BTCUSDT-1m-2026-01.csv", rows)

    provider = BinancePublicDataProvider()
    frame = provider.fetch_candles(
        "BTCUSDT",
        "M1",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
        file_path=zip_path,
    )

    assert list(frame["time"]) == [
        pd.Timestamp("2026-01-01 00:00:00", tz="UTC"),
        pd.Timestamp("2026-01-01 00:01:00", tz="UTC"),
    ]
    assert frame.iloc[0]["open"] == 42000
    assert frame.iloc[1]["close"] == 42150
    assert frame.iloc[0]["real_volume"] == 12.5
    assert frame.iloc[0]["tick_volume"] == 120


def test_binance_provider_builds_monthly_archive_urls() -> None:
    provider = BinancePublicDataProvider(base_url="https://example.test")

    url = provider._archive_url("spot", "monthly", "ETHUSDT", "1m", "2026-01")

    assert url == "https://example.test/data/spot/monthly/klines/ETHUSDT/1m/ETHUSDT-1m-2026-01.zip"
