from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from ict.api import app as api_app
from ict.api import data_management
from ict.api.data_management import DataFetchJobRequest


def test_coverage_row_freshness_uses_latest_complete_day() -> None:
    now = datetime(2026, 7, 7, 15, 45, tzinfo=timezone.utc)

    row = data_management._coverage_row_payload(
        {
            "symbol_code": "EURUSD",
            "local_source": "dukascopy",
            "source_type": "dukascopy",
            "candle_rows": 100,
            "last_candle_time": datetime(2026, 7, 6, 23, 59, tzinfo=timezone.utc),
            "flagged_candles": 0,
        },
        now=now,
    )

    assert row["complete_day_ok"]
    assert not row["today_present"]
    assert row["freshness_status"] == "complete_day_ok"


def test_resolve_channel_mapping() -> None:
    assert data_management.resolve_channel({"source_type": "dukascopy"}, "auto") == "R2"
    assert data_management.resolve_channel({"source_type": "databento"}, "auto") == "Databento"
    assert data_management.resolve_channel({"source_type": "binance_public"}, "auto") == "R2"
    assert data_management.resolve_channel({"source_type": "mt5"}, "auto") == "R2"
    assert data_management.resolve_channel({"source_type": "dukascopy"}, "r2") == "R2"


def test_fetch_forced_channel_skips_incompatible_asset() -> None:
    request = DataFetchJobRequest.model_validate(
        {"channel": "databento", "assets": [{"symbol_code": "EURUSD", "source_name": "dukascopy"}]}
    )

    result = data_management.fetch_missing_for_row(
        {
            "symbol_code": "EURUSD",
            "source_name": "dukascopy",
            "source_type": "dukascopy",
            "local_last": None,
            "complete_day_ok": False,
        },
        request,
        now=datetime(2026, 7, 7, tzinfo=timezone.utc),
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "channel_not_applicable"


def test_fetch_r2_restores_archive_when_selected(monkeypatch) -> None:
    calls = {}

    class Result:
        rows_read = 5
        rows_written = 5
        rows_inserted = 4
        rows_updated = 1
        partitions = [{"day": "2026-07-06"}]
        missing = []
        skipped = []

    def fake_restore(**kwargs):
        calls.update(kwargs)
        return Result()

    monkeypatch.setattr(data_management, "archive_configured", lambda: True)
    monkeypatch.setattr(data_management, "restore_from_r2", fake_restore)
    request = DataFetchJobRequest.model_validate(
        {"channel": "r2", "assets": [{"symbol_code": "BTCUSD", "source_name": "binance"}]}
    )
    now = datetime(2026, 7, 7, 15, 45, tzinfo=timezone.utc)

    result = data_management.fetch_missing_for_row(
        {
            "symbol_code": "BTCUSD",
            "source_name": "binance",
            "source_type": "binance_public",
            "local_last": None,
            "complete_day_ok": False,
        },
        request,
        now=now,
    )

    assert result["status"] == "completed"
    assert result["rows_written"] == 5
    assert calls["symbols"] == ["BTCUSD"]
    assert calls["source_names"] == ["binance"]
    assert calls["skip_existing_local"] is True


def test_fetch_r2_resumes_from_latest_local_candle(monkeypatch) -> None:
    calls = {}
    local_last = datetime(2026, 7, 6, 18, 30, tzinfo=timezone.utc)

    class Result:
        rows_read = 1
        rows_written = 1
        rows_inserted = 1
        rows_updated = 0
        partitions = [{"day": "2026-07-06"}]
        missing = []
        skipped = []

    def fake_restore(**kwargs):
        calls.update(kwargs)
        return Result()

    monkeypatch.setattr(data_management, "archive_configured", lambda: True)
    monkeypatch.setattr(data_management, "restore_from_r2", fake_restore)
    request = DataFetchJobRequest.model_validate(
        {
            "channel": "r2",
            "assets": [{"symbol_code": "EURUSD", "source_name": "dukascopy"}],
            "fallback_days": 180,
            "overlap_minutes": 15,
        }
    )

    data_management.fetch_missing_for_row(
        {
            "symbol_code": "EURUSD",
            "source_name": "dukascopy",
            "source_type": "dukascopy",
            "local_last": local_last.isoformat(),
            "complete_day_ok": False,
        },
        request,
        now=datetime(2026, 7, 8, 12, tzinfo=timezone.utc),
    )

    assert calls["since"] == local_last - timedelta(minutes=15)


def test_data_routes_are_patchable_without_database(monkeypatch) -> None:
    client = TestClient(api_app.app)

    monkeypatch.setattr(api_app, "fetch_data_coverage", lambda: {"rows": [{"symbol_code": "EURUSD"}]})
    monkeypatch.setattr(api_app, "fetch_data_api_usage", lambda: {"rows": [{"fetch_channel": "R2"}]})

    assert client.get("/api/data/coverage").json() == {"rows": [{"symbol_code": "EURUSD"}]}
    assert client.get("/api/data/api-usage").json() == {"rows": [{"fetch_channel": "R2"}]}


def test_data_settings_exposes_archive_cache_dir(monkeypatch) -> None:
    monkeypatch.setattr(
        data_management,
        "get_settings",
        lambda: SimpleNamespace(
            databento_api_key=None,
            market_archive_cache_dir="E:/tradinglab/.cache/market_archive",
            market_archive_max_bucket_gb=10,
        ),
    )
    monkeypatch.setattr(data_management, "archive_configured", lambda: True)
    monkeypatch.setattr(
        data_management,
        "archive_bucket_usage",
        lambda max_bytes=None: SimpleNamespace(as_dict=lambda: {"max_bytes": max_bytes, "total_gb": 0.25}),
    )

    payload = data_management._data_settings_payload()

    assert payload["r2_configured"] is True
    assert payload["archive_cache_dir"] == "E:/tradinglab/.cache/market_archive"
    assert payload["r2_bucket_usage"]["max_bytes"] == 10 * 1024 * 1024 * 1024
