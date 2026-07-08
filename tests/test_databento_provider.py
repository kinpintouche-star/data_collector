from __future__ import annotations

import zipfile
from datetime import datetime, timezone

import pandas as pd
import pytest

from ict.data.providers.databento_provider import DatabentoHistoricalProvider


def test_databento_provider_reads_ohlcv_zip_with_fixed_price_scale(tmp_path) -> None:
    zip_path = tmp_path / "XNAS-sample.zip"
    rows = "\n".join(
        [
            "ts_event,rtype,publisher_id,instrument_id,open,high,low,close,volume,symbol",
            "1654864200000000000,33,2,7370,265760000000,265760000000,261010000000,261060000000,3006,MSFT",
            "1654864260000000000,33,2,7370,261120000000,262070000000,261000000000,261640000000,1616,MSFT",
        ]
    )
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("xnas-itch-20220610.ohlcv-1m.csv", rows)

    provider = DatabentoHistoricalProvider()
    frame = provider.fetch_candles(
        "MSFT",
        "M1",
        datetime(2022, 6, 10, 12, 30, tzinfo=timezone.utc),
        datetime(2022, 6, 10, 12, 31, tzinfo=timezone.utc),
        file_path=zip_path,
        schema="ohlcv-1m",
    )

    assert list(frame["time"]) == [
        pd.Timestamp("2022-06-10 12:30:00", tz="UTC"),
        pd.Timestamp("2022-06-10 12:31:00", tz="UTC"),
    ]
    assert frame.iloc[0]["open"] == pytest.approx(265.76)
    assert frame.iloc[0]["low"] == pytest.approx(261.01)
    assert frame.iloc[1]["close"] == pytest.approx(261.64)
    assert frame.iloc[0]["tick_volume"] == 3006


def test_databento_provider_rejects_download_above_cost_limit() -> None:
    class FakeMetadata:
        def get_cost(self, **kwargs) -> float:
            return 12.50

    class FakeTimeseries:
        def get_range(self, **kwargs):
            raise AssertionError("paid data should not be requested")

    class FakeClient:
        metadata = FakeMetadata()
        timeseries = FakeTimeseries()

    provider = DatabentoHistoricalProvider(client=FakeClient())

    with pytest.raises(ValueError, match="above configured max_cost_usd"):
        provider.fetch_candles(
            "MNQ.c.0",
            "M1",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 7, 1, tzinfo=timezone.utc),
            dataset="GLBX.MDP3",
            max_cost_usd=5,
        )


def test_databento_provider_inferrs_symbology_type() -> None:
    provider = DatabentoHistoricalProvider()

    assert provider._infer_stype_in("MNQ.c.0") == "continuous"
    assert provider._infer_stype_in("MNQ.FUT") == "parent"
    assert provider._infer_stype_in("MSFT") == "raw_symbol"
