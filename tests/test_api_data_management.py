from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from ict.api import app as api_app
from ict.api import data_management
from ict.api.data_management import DataFetchJobRequest
from ict.live.sync import LiveSyncResult


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
    assert data_management.resolve_channel({"source_type": "dukascopy"}, "neon") == "Neon"
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


def test_fetch_neon_syncs_when_remote_is_newer(monkeypatch) -> None:
    calls = {}

    class Settings:
        live_remote_database_url = "postgresql://remote"

    def fake_sync(remote_url, since, until, symbols, limit):
        calls.update({"remote_url": remote_url, "since": since, "until": until, "symbols": symbols, "limit": limit})
        return LiveSyncResult(rows_read=3, rows_written=3, rows_inserted=2, rows_updated=1, groups=[])

    monkeypatch.setattr(data_management, "get_settings", lambda: Settings())
    monkeypatch.setattr(data_management, "sync_remote_candles", fake_sync)
    request = DataFetchJobRequest.model_validate(
        {"channel": "neon", "assets": [{"symbol_code": "BTCUSD", "source_name": "binance"}], "neon_limit": 1234}
    )

    result = data_management.fetch_missing_for_row(
        {
            "symbol_code": "BTCUSD",
            "source_name": "binance",
            "source_type": "binance_public",
            "local_last": "2026-07-06T00:00:00+00:00",
            "neon_last": "2026-07-06T00:10:00+00:00",
            "complete_day_ok": False,
        },
        request,
    )

    assert result["status"] == "completed"
    assert result["rows_written"] == 3
    assert calls["remote_url"] == "postgresql://remote"
    assert calls["symbols"] == ["BTCUSD"]
    assert calls["limit"] == 1234


def test_fetch_neon_attempts_sync_when_remote_state_is_unknown(monkeypatch) -> None:
    calls = {}

    class Settings:
        live_remote_database_url = "postgresql://remote"

    def fake_sync(remote_url, since, until, symbols, limit):
        calls.update({"remote_url": remote_url, "since": since, "until": until, "symbols": symbols, "limit": limit})
        return LiveSyncResult(rows_read=0, rows_written=0, rows_inserted=0, rows_updated=0, groups=[])

    monkeypatch.setattr(data_management, "get_settings", lambda: Settings())
    monkeypatch.setattr(data_management, "sync_remote_candles", fake_sync)
    request = DataFetchJobRequest.model_validate(
        {"channel": "neon", "assets": [{"symbol_code": "BTCUSD", "source_name": "binance"}], "neon_limit": 1234}
    )
    now = datetime(2026, 7, 7, 15, 45, tzinfo=timezone.utc)

    result = data_management.fetch_missing_for_row(
        {
            "symbol_code": "BTCUSD",
            "source_name": "binance",
            "source_type": "binance_public",
            "local_last": "2026-07-06T00:00:00+00:00",
            "neon_last": None,
            "complete_day_ok": False,
        },
        request,
        now=now,
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "no_neon_data"
    assert calls["remote_url"] == "postgresql://remote"
    assert calls["until"] == now
    assert calls["symbols"] == ["BTCUSD"]


def test_fetch_r2_restores_archive_when_selected(monkeypatch) -> None:
    calls = {}

    class Result:
        rows_read = 5
        rows_written = 5
        rows_inserted = 4
        rows_updated = 1
        partitions = [{"day": "2026-07-06"}]
        missing = []

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


def test_data_routes_are_patchable_without_database(monkeypatch) -> None:
    client = TestClient(api_app.app)

    monkeypatch.setattr(api_app, "fetch_data_coverage", lambda: {"rows": [{"symbol_code": "EURUSD"}]})
    monkeypatch.setattr(api_app, "fetch_data_api_usage", lambda: {"rows": [{"fetch_channel": "Neon"}]})

    assert client.get("/api/data/coverage").json() == {"rows": [{"symbol_code": "EURUSD"}]}
    assert client.get("/api/data/api-usage").json() == {"rows": [{"fetch_channel": "Neon"}]}
