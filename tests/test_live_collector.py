from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from uuid import UUID

import pandas as pd

from ict.dashboard.data import DASHBOARD_QUERIES, PAGES
from ict.live.config import load_live_sources
from ict.live import collector as live_collector
from ict.live import providers as live_providers
from ict.live import sync as live_sync


def test_live_sources_config_defaults_to_daily_six_month_retention() -> None:
    sources = load_live_sources("configs/live_sources.yaml")

    assert len(sources) == 40
    assert sum(1 for source in sources if source.enabled) == 32
    assert {source.provider for source in sources if source.enabled} == {"coinbase", "dukascopy_node"}
    assert {source.provider for source in sources if not source.enabled} == {"pending_cloud_source", "databento"}
    btc = next(source for source in sources if source.symbol_code == "BTCUSD")
    assert btc.provider_symbol == "BTC-USD"
    assert btc.fallback_provider == "kraken"
    assert btc.fallback_provider_symbol == "XBTUSD"
    mnq = next(source for source in sources if source.symbol_code == "MNQ")
    assert mnq.provider == "databento"
    assert not mnq.enabled
    assert mnq.collection_mode == "manual_paid_databento"
    assert mnq.dataset == "GLBX.MDP3"
    assert mnq.max_cost_usd == 1.0
    assert {source.poll_interval_minutes for source in sources} == {1440}
    assert {source.retention_days for source in sources} == {30}
    assert {source.symbol_code for source in sources if source.priority == 10} == {
        "EURUSD",
        "GER40",
        "NAS100",
        "BTCUSD",
        "ETHUSD",
    }


def test_dashboard_exposes_live_collector_page() -> None:
    assert "Live Collector" in PAGES
    assert "mart_live_collector" in DASHBOARD_QUERIES["Live Collector"]
    assert "collector_runs" in DASHBOARD_QUERIES["Live Runs"]
    assert "collector_incidents" in DASHBOARD_QUERIES["Live Incidents"]
    assert "LAG(c.time_open)" in DASHBOARD_QUERIES["Gaps"]


def test_remote_sync_counts_existing_rows_as_updates(monkeypatch) -> None:
    frame = pd.DataFrame(
        {
            "symbol_code": ["BTCUSD", "BTCUSD"],
            "source_name": ["binance", "binance"],
            "source_symbol": ["BTCUSDT", "BTCUSDT"],
            "timeframe": ["M1", "M1"],
            "time_open": [
                datetime(2026, 7, 5, 0, 0, tzinfo=timezone.utc),
                datetime(2026, 7, 5, 0, 1, tzinfo=timezone.utc),
            ],
            "open": [100, 101],
            "high": [102, 103],
            "low": [99, 100],
            "close": [101, 102],
            "tick_volume": [10, 20],
            "real_volume": [1, 2],
            "spread": [None, None],
            "quality_flags": [{}, {}],
            "metadata": [{}, {}],
        }
    )

    class DummyAliasRepository:
        def __init__(self, session):
            pass

        def resolve(self, symbol_code: str, source_name: str):
            return SimpleNamespace(
                symbol_id=UUID("00000000-0000-0000-0000-000000000001"),
                source_id=UUID("00000000-0000-0000-0000-000000000002"),
            )

    class DummyCandleRepository:
        def __init__(self, session):
            pass

        def rows_for_frame(self, symbol_id, source_id, source_symbol, timeframe, storage_frame):
            return [
                {
                    "time_open": row["time_open"],
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "tick_volume": row["tick_volume"],
                    "real_volume": row["real_volume"],
                    "spread": row["spread"],
                    "quality_flags": row["quality_flags"],
                    "metadata": row["source_metadata"],
                }
                for row in storage_frame.to_dict(orient="records")
            ]

        def count_existing_candles(self, symbol_id, source_id, timeframe, time_opens):
            return 1

        def upsert_candles(self, rows):
            return len(list(rows))

    @contextmanager
    def dummy_session_scope():
        yield object()

    monkeypatch.setattr(live_sync, "AliasRepository", DummyAliasRepository)
    monkeypatch.setattr(live_sync, "CandleRepository", DummyCandleRepository)
    monkeypatch.setattr(live_sync, "session_scope", dummy_session_scope)

    result = live_sync.upsert_remote_frame(frame)

    assert result.rows_read == 2
    assert result.rows_written == 2
    assert result.rows_inserted == 1
    assert result.rows_updated == 1


def test_local_to_remote_sync_chunks_and_aggregates(monkeypatch) -> None:
    calls = []

    def fake_read_local_live_candles(since, until, symbols, limit, config):
        calls.append({"since": since, "until": until, "symbols": symbols, "limit": limit, "config": config})
        return pd.DataFrame({"time_open": [since]})

    def fake_upsert_frame_to_remote_compact(frame, database_url):
        index = len(calls)
        return live_sync.LiveSyncResult(
            rows_read=len(frame),
            rows_written=len(frame),
            rows_inserted=1 if index == 1 else 0,
            rows_updated=0 if index == 1 else 1,
            groups=[
                {
                    "symbol": "BTCUSD",
                    "source": "binance",
                    "source_symbol": "BTCUSDT",
                    "timeframe": "M1",
                    "rows": len(frame),
                    "inserted": 1 if index == 1 else 0,
                    "updated": 0 if index == 1 else 1,
                }
            ],
        )

    monkeypatch.setattr(live_sync, "read_local_live_candles", fake_read_local_live_candles)
    monkeypatch.setattr(live_sync, "upsert_frame_to_remote_compact", fake_upsert_frame_to_remote_compact)
    monkeypatch.setattr(live_sync, "refresh_remote_source_state", lambda *args, **kwargs: 1)

    result = live_sync.sync_local_candles_to_remote(
        "postgresql://example",
        since=datetime(2026, 7, 1, tzinfo=timezone.utc),
        until=datetime(2026, 7, 3, tzinfo=timezone.utc),
        symbols=["BTCUSD"],
        limit=100,
        chunk_days=1,
        config="configs/live_sources.yaml",
    )

    assert len(calls) == 2
    assert all(call["symbols"] == ["BTCUSD"] for call in calls)
    assert all(call["limit"] == 100 for call in calls)
    assert result.rows_read == 2
    assert result.rows_written == 2
    assert result.rows_inserted == 1
    assert result.rows_updated == 1
    assert result.groups == [
        {
            "symbol": "BTCUSD",
            "source": "binance",
            "source_symbol": "BTCUSDT",
            "timeframe": "M1",
            "rows": 2,
            "inserted": 1,
            "updated": 1,
        }
    ]


def test_coinbase_live_provider_normalizes_and_ignores_open_candles() -> None:
    source = next(source for source in load_live_sources("configs/live_sources.yaml") if source.symbol_code == "BTCUSD")
    frame = live_providers.normalize_coinbase_rows(
        source,
        [
            [1767225660, 2, 3, 2.5, 2.75, 20],
            [1767225600, 1, 2, 1.5, 1.75, 10],
            [1767225720, 3, 4, 3.5, 3.75, 30],
        ],
        datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 0, 3, tzinfo=timezone.utc),
        now=datetime(2026, 1, 1, 0, 2, 30, tzinfo=timezone.utc),
    )

    assert frame["time_open"].dt.strftime("%H:%M").tolist() == ["00:00", "00:01"]
    assert frame.iloc[0]["symbol_code"] == "BTCUSD"
    assert frame.iloc[0]["source_name"] == "binance"
    assert frame.iloc[0]["source_symbol"] == "BTCUSDT"
    assert frame.iloc[0]["metadata"]["provider"] == "coinbase"


def test_dukascopy_node_normalizes_and_ignores_open_candles() -> None:
    source = next(source for source in load_live_sources("configs/live_sources.yaml") if source.symbol_code == "EURUSD")
    rows = pd.DataFrame(
        {
            "timestamp": [1767225600000, 1767225660000, 1767225720000],
            "open": [1.1, 1.2, 1.3],
            "high": [1.2, 1.3, 1.4],
            "low": [1.0, 1.1, 1.2],
            "close": [1.15, 1.25, 1.35],
            "volume": [10, 20, 30],
        }
    )

    frame = live_providers.normalize_dukascopy_node_rows(
        source,
        rows,
        datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 0, 3, tzinfo=timezone.utc),
        now=datetime(2026, 1, 1, 0, 2, 30, tzinfo=timezone.utc),
    )

    assert frame["time_open"].dt.strftime("%H:%M").tolist() == ["00:00", "00:01"]
    assert frame.iloc[0]["symbol_code"] == "EURUSD"
    assert frame.iloc[0]["source_name"] == "dukascopy"
    assert frame.iloc[0]["metadata"]["provider"] == "dukascopy_node"


def test_databento_live_provider_respects_cost_guard(monkeypatch) -> None:
    source = next(source for source in load_live_sources("configs/live_sources.yaml") if source.symbol_code == "MNQ")
    calls = {}

    class FakeProvider:
        def fetch_candles(self, source_symbol, timeframe, start, end, **kwargs):
            calls.update(
                {
                    "source_symbol": source_symbol,
                    "timeframe": timeframe,
                    "dataset": kwargs["dataset"],
                    "max_cost_usd": kwargs["max_cost_usd"],
                }
            )
            return pd.DataFrame(
                {
                    "time": pd.date_range("2026-01-01T00:00:00Z", periods=2, freq="min"),
                    "open": [25000, 25001],
                    "high": [25002, 25003],
                    "low": [24999, 25000],
                    "close": [25001, 25002],
                    "tick_volume": [100, 120],
                    "real_volume": [100, 120],
                }
            )

    monkeypatch.setattr(live_providers, "DatabentoHistoricalProvider", lambda: FakeProvider())

    frame = live_providers.fetch_databento_candles(
        source,
        datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc),
        now=datetime(2026, 1, 1, 0, 3, tzinfo=timezone.utc),
    )

    assert calls == {
        "source_symbol": "MNQ.c.0",
        "timeframe": "M1",
        "dataset": "GLBX.MDP3",
        "max_cost_usd": 1.0,
    }
    assert len(frame) == 2
    assert frame.iloc[0]["metadata"]["provider"] == "databento"


def test_oanda_live_provider_normalizes_and_ignores_incomplete_candles() -> None:
    source = SimpleNamespace(
        symbol_code="EURUSD",
        source_name="oanda",
        source_symbol="EUR_USD",
        provider_symbol="EUR_USD",
        timeframe="M1",
    )
    frame = live_providers.normalize_oanda_rows(
        source,
        [
            {
                "complete": True,
                "time": "2026-01-01T00:00:00.000000000Z",
                "mid": {"o": "1.1000", "h": "1.1010", "l": "1.0990", "c": "1.1005"},
                "volume": 12,
            },
            {
                "complete": True,
                "time": "2026-01-01T00:01:00.000000000Z",
                "mid": {"o": "1.1005", "h": "1.1020", "l": "1.1000", "c": "1.1015"},
                "volume": 15,
            },
            {
                "complete": False,
                "time": "2026-01-01T00:02:00.000000000Z",
                "mid": {"o": "1.1015", "h": "1.1030", "l": "1.1010", "c": "1.1025"},
                "volume": 9,
            },
        ],
        datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 0, 3, tzinfo=timezone.utc),
        now=datetime(2026, 1, 1, 0, 2, 30, tzinfo=timezone.utc),
    )

    assert frame["time_open"].dt.strftime("%H:%M").tolist() == ["00:00", "00:01"]
    assert frame.iloc[0]["symbol_code"] == "EURUSD"
    assert frame.iloc[0]["source_name"] == "oanda"
    assert frame.iloc[0]["source_symbol"] == "EUR_USD"
    assert frame.iloc[0]["tick_volume"] == 12
    assert frame.iloc[0]["metadata"]["provider"] == "oanda"


def test_remote_collector_uploads_asset_frame_in_chunks() -> None:
    source = load_live_sources("configs/live_sources.yaml")[0]
    frame = pd.DataFrame(
        {
            "symbol_code": ["BTCUSD", "BTCUSD", "BTCUSD"],
            "source_name": ["binance", "binance", "binance"],
            "source_symbol": ["BTCUSDT", "BTCUSDT", "BTCUSDT"],
            "timeframe": ["M1", "M1", "M1"],
            "time_open": pd.date_range("2026-01-01T00:00:00Z", periods=3, freq="min"),
            "open": [1, 2, 3],
            "high": [2, 3, 4],
            "low": [0, 1, 2],
            "close": [1.5, 2.5, 3.5],
            "tick_volume": [None, None, None],
            "real_volume": [10, 20, 30],
            "spread": [None, None, None],
            "quality_flags": [{}, {}, {}],
            "metadata": [{}, {}, {}],
        }
    )
    chunk_sizes = []

    def fake_upsert(chunk, database_url):
        chunk_sizes.append(len(chunk))
        return live_sync.LiveSyncResult(
            rows_read=len(chunk),
            rows_written=len(chunk),
            rows_inserted=len(chunk),
            rows_updated=0,
            groups=[
                {
                    "symbol": "BTCUSD",
                    "source": "binance",
                    "source_symbol": "BTCUSDT",
                    "timeframe": "M1",
                    "rows": len(chunk),
                    "inserted": len(chunk),
                    "updated": 0,
                }
            ],
        )

    result = live_collector.upload_asset_frame(
        "postgresql://example",
        source,
        frame,
        upload_chunk_rows=2,
        upsert_frame=fake_upsert,
    )

    assert chunk_sizes == [2, 1]
    assert result.status == "ok"
    assert result.rows_written == 3
    assert result.rows_inserted == 3


def test_remote_collector_fetches_assets_and_uploads_when_ready(monkeypatch) -> None:
    calls = {"finished": False, "refreshed": [], "resolved": []}

    monkeypatch.setattr(live_collector, "create_remote_collector_run", lambda *args, **kwargs: "00000000-0000-0000-0000-000000000001")
    monkeypatch.setattr(
        live_collector,
        "finish_remote_collector_run",
        lambda *args, **kwargs: calls.__setitem__("finished", True),
    )
    monkeypatch.setattr(
        live_collector,
        "refresh_remote_source_state",
        lambda _url, symbols, config: calls["refreshed"].extend(symbols),
    )
    monkeypatch.setattr(
        live_collector,
        "resolve_remote_incident",
        lambda _url, source: calls["resolved"].append(source.symbol_code),
    )
    monkeypatch.setattr(live_collector, "record_remote_source_failure", lambda *args, **kwargs: None)

    def fake_fetch(source, since, until):
        return pd.DataFrame(
            {
                "symbol_code": [source.symbol_code],
                "source_name": [source.source_name],
                "source_symbol": [source.source_symbol],
                "timeframe": [source.timeframe],
                "time_open": [since],
                "open": [1],
                "high": [2],
                "low": [0],
                "close": [1.5],
                "tick_volume": [None],
                "real_volume": [10],
                "spread": [None],
                "quality_flags": [{}],
                "metadata": [{}],
            }
        )

    def fake_upsert(frame, database_url):
        return live_sync.LiveSyncResult(
            rows_read=len(frame),
            rows_written=len(frame),
            rows_inserted=len(frame),
            rows_updated=0,
            groups=[],
        )

    summary = live_collector.collect_remote_live(
        "postgresql://example",
        since=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        until=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
        symbols=["BTCUSD", "ETHUSD"],
        max_workers=2,
        submit_pause_seconds=0,
        fetch_source=fake_fetch,
        upsert_frame=fake_upsert,
    )

    assert calls["finished"] is True
    assert sorted(calls["refreshed"]) == ["BTCUSD", "ETHUSD"]
    assert sorted(calls["resolved"]) == ["BTCUSD", "ETHUSD"]
    assert summary.assets_succeeded == 2
    assert summary.rows_written == 2


def test_remote_collector_filters_priority_and_writes_jsonl_dry_run(tmp_path) -> None:
    log_path = tmp_path / "collector.jsonl"

    summary = live_collector.collect_remote_live(
        "dry-run",
        since=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        until=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
        max_priority=10,
        dry_run=True,
        log_path=log_path,
    )

    assert summary.status == "dry_run"
    assert summary.assets_requested == 5
    assert {result.symbol_code for result in summary.results} == {
        "EURUSD",
        "GER40",
        "NAS100",
        "BTCUSD",
        "ETHUSD",
    }
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["run_started", "run_completed"]
    assert "postgresql://" not in json.dumps(events).lower()
