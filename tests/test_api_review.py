from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
from fastapi.testclient import TestClient

from ict.api import app as api_app
from ict.api.review import _annotation_payload, _gap_summary, _infer_fib, _risk_reward_payload


def test_infer_fib_from_setup_events() -> None:
    trade = {
        "direction": "bullish",
        "entry_time": datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        "entry_price": Decimal("104"),
        "sl": Decimal("99"),
        "tp": Decimal("112"),
    }
    events = [
        {
            "event_type": "M15_DOUBLE_SWING_VALIDATED",
            "event_time": datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc),
            "price": None,
            "metadata": {
                "s2_time": datetime(2025, 1, 1, 9, 30, tzinfo=timezone.utc),
                "s2_price": 100,
            },
        },
        {
            "event_type": "LEG_FOUND",
            "event_time": datetime(2025, 1, 1, 11, 0, tzinfo=timezone.utc),
            "price": 120,
            "metadata": {"leg_end_time": datetime(2025, 1, 1, 10, 45, tzinfo=timezone.utc)},
        },
        {
            "event_type": "OTE_CREATED",
            "event_time": datetime(2025, 1, 1, 11, 0, tzinfo=timezone.utc),
            "price": None,
            "metadata": {"ote_bottom": 104.2, "ote_top": 107.64},
        },
    ]

    fib = _infer_fib(events, trade, pd.DataFrame())

    assert fib["available"] is True
    assert fib["source"] == "events"
    assert fib["visible_timeframes"] == ["M15", "M5", "M1"]
    assert fib["anchor_start"]["price"] == 100.0
    assert fib["anchor_end"]["price"] == 120.0
    assert fib["ote_zone"] == {"bottom": 104.2, "top": 107.64}
    assert {level["label"] for level in fib["levels"]} >= {"0.618", "0.79"}


def test_risk_reward_payload_handles_bearish_trade() -> None:
    payload = _risk_reward_payload(
        {
            "entry_price": Decimal("100"),
            "sl": Decimal("105"),
            "tp": Decimal("90"),
            "exit_price": Decimal("90"),
            "rr": Decimal("2"),
        }
    )

    assert payload["risk"] == 5.0
    assert payload["reward"] == 10.0
    assert payload["planned_rr"] == 2.0
    assert payload["risk_zone"] == {"bottom": 100.0, "top": 105.0}
    assert payload["reward_zone"] == {"bottom": 90.0, "top": 100.0}


def test_annotation_payload_places_structured_trade_objects() -> None:
    trade = {
        "setup_id": "setup-1",
        "direction": "bearish",
        "entry_time": datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        "exit_time": datetime(2025, 1, 1, 12, 30, tzinfo=timezone.utc),
        "tp": Decimal("90"),
    }
    events = [
        {
            "setup_id": "setup-1",
            "event_type": "H1_SIGNAL",
            "event_time": datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc),
            "direction": "bearish",
            "metadata": {
                "c2_time": datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc),
                "c1_high": 110,
                "c1_mid": 100,
                "c1_low": 90,
            },
        },
        {
            "setup_id": "setup-1",
            "event_type": "M15_DOUBLE_SWING_VALIDATED",
            "event_time": datetime(2025, 1, 1, 10, 15, tzinfo=timezone.utc),
            "direction": "bearish",
            "metadata": {
                "s1_time": datetime(2025, 1, 1, 8, 30, tzinfo=timezone.utc),
                "s1_price": 108,
                "s2_time": datetime(2025, 1, 1, 9, 30, tzinfo=timezone.utc),
                "s2_price": 106,
            },
        },
        {
            "setup_id": "setup-1",
            "event_type": "LEG_FOUND",
            "event_time": datetime(2025, 1, 1, 11, 0, tzinfo=timezone.utc),
            "price": 92,
            "direction": "bearish",
            "metadata": {"leg_end_time": datetime(2025, 1, 1, 10, 45, tzinfo=timezone.utc)},
        },
        {
            "setup_id": "setup-1",
            "event_type": "OB_SELECTED",
            "event_time": datetime(2025, 1, 1, 11, 30, tzinfo=timezone.utc),
            "direction": "bearish",
            "metadata": {
                "pd_time": datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc),
                "pd_bottom": 101,
                "pd_top": 103,
                "pd_mid": 102,
            },
        },
    ]
    fib = {
        "available": True,
        "visible_timeframes": ["M15", "M5", "M1"],
        "anchor_start": {"time": "2025-01-01T09:30:00+00:00", "price": 106},
        "anchor_end": {"time": "2025-01-01T10:45:00+00:00", "price": 92},
        "ote_zone": {"bottom": 99, "top": 101},
    }

    annotations = _annotation_payload(trade, events, fib)

    assert {zone["kind"] for zone in annotations["zones"]} >= {"OB", "OTE"}
    assert {swing["label"] for swing in annotations["swings"]} == {"S1", "S2", "LEG"}
    assert any(level["label"] == "CRT OBJ" and level["price"] == 90.0 for level in annotations["levels"])


def test_gap_summary_reports_missing_m1_candles() -> None:
    times = list(pd.date_range("2025-01-01 00:00", periods=5, freq="min", tz="UTC"))
    times += list(pd.date_range("2025-01-01 00:10", periods=5, freq="min", tz="UTC"))
    candles = pd.DataFrame(
        {
            "time_open": times,
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
        }
    )

    summary = _gap_summary(candles)

    assert summary["gap_count"] == 1
    assert summary["missing_candles"] == 5
    assert summary["largest_gap_candles"] == 5


def test_api_routes_are_patchable_without_database(monkeypatch) -> None:
    client = TestClient(api_app.app)

    monkeypatch.setattr(api_app, "fetch_runs", lambda limit=100: [{"id": "run-1", "total_trades": 2}])
    monkeypatch.setattr(api_app, "fetch_run_trades", lambda run_id: [{"id": "trade-1", "run_id": run_id}])
    monkeypatch.setattr(api_app, "fetch_run_analytics", lambda run_id: {"run": {"id": run_id}})
    monkeypatch.setattr(api_app, "build_trade_review", lambda trade_id: {"trade": {"id": trade_id}})

    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/runs").json() == [{"id": "run-1", "total_trades": 2}]
    assert client.get("/api/runs/run-1/trades").json() == [{"id": "trade-1", "run_id": "run-1"}]
    assert client.get("/api/runs/run-1/analytics").json() == {"run": {"id": "run-1"}}
    assert client.get("/api/trades/trade-1/review").json() == {"trade": {"id": "trade-1"}}
