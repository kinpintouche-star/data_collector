from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ict.api.analytics import build_analytics_payload


def test_build_analytics_payload_computes_core_metrics() -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    trades = [
        {
            "id": "t1",
            "direction": "bullish",
            "entry_time": base,
            "exit_time": base + timedelta(minutes=20),
            "pnl": 100,
            "rr": 2,
            "pd_type": "OB",
            "session_name": "London",
            "exit_reason": "TP",
        },
        {
            "id": "t2",
            "direction": "bearish",
            "entry_time": base + timedelta(days=1),
            "exit_time": base + timedelta(days=1, minutes=15),
            "pnl": -50,
            "rr": -1,
            "pd_type": "FVG",
            "session_name": "NY",
            "exit_reason": "SL",
        },
        {
            "id": "t3",
            "direction": "bullish",
            "entry_time": base + timedelta(days=32),
            "exit_time": base + timedelta(days=32, minutes=10),
            "pnl": -25,
            "rr": -0.5,
            "pd_type": "OB",
            "session_name": "London",
            "exit_reason": "SL",
        },
    ]
    payload = build_analytics_payload(_run(), trades, [], [{"event_type": "H1_SIGNAL", "count": 4}])

    assert payload["summary"]["total_trades"] == 3
    assert payload["summary"]["wins"] == 1
    assert payload["summary"]["losses"] == 2
    assert payload["summary"]["net_pnl"] == 25.0
    assert payload["summary"]["profit_factor"] == 100 / 75
    assert payload["summary"]["max_consecutive_losses"] == 2
    assert [row["month"] for row in payload["monthly"]] == ["2026-01", "2026-02"]
    assert payload["breakdowns"]["pd_type"][0]["name"] == "OB"
    assert payload["event_funnel"][0] == {"event_type": "H1_SIGNAL", "count": 4}


def test_build_analytics_payload_flags_underperforming_slice() -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    trades = []
    for index in range(12):
        trades.append(
            {
                "id": f"l{index}",
                "direction": "bearish",
                "entry_time": base + timedelta(hours=index),
                "exit_time": base + timedelta(hours=index, minutes=10),
                "pnl": -10,
                "rr": -1,
                "pd_type": "FVG",
                "session_name": "London",
                "exit_reason": "SL",
            }
        )
    for index in range(18):
        trades.append(
            {
                "id": f"w{index}",
                "direction": "bullish",
                "entry_time": base + timedelta(days=1, hours=index),
                "exit_time": base + timedelta(days=1, hours=index, minutes=10),
                "pnl": 8,
                "rr": 1,
                "pd_type": "OB",
                "session_name": "NY",
                "exit_reason": "TP",
            }
        )

    payload = build_analytics_payload(_run(), trades, [], [])

    assert any("direction: bearish" in item["title"] for item in payload["diagnostics"])
    assert any(item["severity"] == "warning" for item in payload["diagnostics"])


def test_build_analytics_payload_includes_symbol_comparison() -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    trades = [
        {
            "id": "eur-1",
            "symbol_code": "EURUSD",
            "source_name": "dukascopy",
            "direction": "bullish",
            "entry_time": base,
            "exit_time": base + timedelta(minutes=20),
            "pnl": 10,
            "rr": 1,
            "pd_type": "OB",
            "session_name": "London",
            "exit_reason": "TP",
        },
        {
            "id": "gbp-1",
            "symbol_code": "GBPUSD",
            "source_name": "dukascopy",
            "direction": "bearish",
            "entry_time": base + timedelta(hours=1),
            "exit_time": base + timedelta(hours=1, minutes=20),
            "pnl": -4,
            "rr": -1,
            "pd_type": "FVG",
            "session_name": "NY",
            "exit_reason": "SL",
        },
    ]

    payload = build_analytics_payload(_run(), trades, [], [])

    assert {row["name"] for row in payload["comparisons"]["symbols"]} == {"EURUSD", "GBPUSD"}
    assert payload["breakdowns"]["symbol"][0]["name"] == "EURUSD"


def _run() -> dict:
    return {
        "run_id": "run-1",
        "status": "completed",
        "run_type": "backtest",
        "start_time": "2026-01-01T00:00:00+00:00",
        "end_time": "2026-02-28T00:00:00+00:00",
        "created_at": "2026-03-01T00:00:00+00:00",
        "initial_balance": 10000,
        "final_balance": 10025,
        "symbol_code": "EURUSD",
        "source_name": "dukascopy",
        "strategy_name": "ICT CRT",
        "strategy_version": "0.1",
        "parameter_set_name": "default",
        "dataset_timeframe": "M1",
    }
