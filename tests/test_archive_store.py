from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pandas as pd

from ict.archive import store as archive_store


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol_code": ["BTCUSD", "BTCUSD"],
            "source_name": ["binance", "binance"],
            "source_symbol": ["BTCUSDT", "BTCUSDT"],
            "timeframe": ["M1", "M1"],
            "time_open": pd.date_range("2026-07-01T00:00:00Z", periods=2, freq="min"),
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.0],
            "close": [101.0, 102.0],
            "tick_volume": [10, 11],
            "real_volume": [1, 2],
            "spread": [None, None],
            "quality_flags": [{"ok": True}, {}],
            "metadata": [{"provider": "coinbase"}, {"provider": "coinbase"}],
        }
    )


def test_archive_export_restore_roundtrip(tmp_path, monkeypatch) -> None:
    key = b"0" * 32
    object_store = archive_store.LocalObjectStore(tmp_path)
    partitions = archive_store.export_frame_to_store(_frame(), object_store, key)

    assert len(partitions) == 1
    manifest = json.loads(object_store.get_bytes(partitions[0].manifest_key).decode("utf-8"))
    assert manifest["data_format"] == "canonical_market_candles_v1"
    assert manifest["rows"] == 2

    captured = {}

    def fake_upsert(frame):
        captured["frame"] = frame
        return archive_store.LiveSyncResult(2, 2, 2, 0, [])

    monkeypatch.setattr(archive_store, "upsert_remote_frame", fake_upsert)

    result = archive_store.restore_from_r2(
        since=datetime(2026, 7, 1, tzinfo=timezone.utc),
        until=datetime(2026, 7, 2, tzinfo=timezone.utc),
        symbols=["BTCUSD"],
        source_names=["binance"],
        store=object_store,
        archive_key=key,
    )

    assert result.status == "completed"
    assert result.rows_written == 2
    assert captured["frame"]["symbol_code"].tolist() == ["BTCUSD", "BTCUSD"]
    assert captured["frame"].iloc[0]["metadata"]["provider"] == "coinbase"


def test_archive_bucket_usage_counts_objects(tmp_path) -> None:
    object_store = archive_store.LocalObjectStore(tmp_path)
    object_store.put_bytes("market-candles/a.bin", b"abc")
    object_store.put_bytes("market-candles/b.bin", b"abcd")

    usage = archive_store.archive_bucket_usage(store=object_store, prefix="market-candles", max_bytes=10)

    assert usage.object_count == 2
    assert usage.total_bytes == 7
    assert usage.remaining_bytes == 3
    assert usage.as_dict()["over_limit"] is False


def test_archive_export_rejects_bucket_budget_excess(tmp_path) -> None:
    key = b"2" * 32
    object_store = archive_store.LocalObjectStore(tmp_path)

    try:
        archive_store.export_frame_to_store(_frame(), object_store, key, max_bucket_bytes=1)
    except ValueError as exc:
        assert "R2 archive bucket budget would be exceeded" in str(exc)
    else:
        raise AssertionError("export_frame_to_store should reject an over-budget upload")

    assert object_store.list_keys("") == []


def test_archive_restore_rejects_wrong_manifest_format(tmp_path) -> None:
    key = b"1" * 32
    object_store = archive_store.LocalObjectStore(tmp_path)
    partition = archive_store.export_frame_to_store(_frame(), object_store, key)[0]
    manifest = json.loads(object_store.get_bytes(partition.manifest_key).decode("utf-8"))
    manifest["data_format"] = "raw_provider_payload"
    object_store.put_bytes(partition.manifest_key, json.dumps(manifest).encode("utf-8"))

    try:
        archive_store.restore_from_r2(
            since=datetime(2026, 7, 1, tzinfo=timezone.utc),
            until=datetime(2026, 7, 2, tzinfo=timezone.utc),
            symbols=["BTCUSD"],
            source_names=["binance"],
            store=object_store,
            archive_key=key,
        )
    except ValueError as exc:
        assert "Unsupported archive data format" in str(exc)
    else:
        raise AssertionError("restore_from_r2 should reject an unknown data_format")


def test_archive_key_helper_requires_32_bytes(monkeypatch) -> None:
    class Settings:
        market_archive_key = base64.b64encode(b"x" * 32).decode("ascii")

    monkeypatch.setattr(archive_store, "get_settings", lambda: Settings())

    assert archive_store._archive_key_from_settings() == b"x" * 32


def test_collect_to_r2_dry_run_excludes_databento() -> None:
    result = archive_store.collect_live_sources_to_r2(dry_run=True)

    symbols = {row.symbol_code for row in result.results}
    providers = {row.provider for row in result.results}
    assert "MNQ" not in symbols
    assert "databento" not in providers
    assert result.status == "dry_run"
