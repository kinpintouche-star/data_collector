from __future__ import annotations

from ict.api.backtests import BacktestLaunchRequest, create_backtest_job, get_backtest_job


def test_create_backtest_job_records_launch_contract() -> None:
    request = BacktestLaunchRequest.model_validate(
        {
            "strategy_config": "configs/strategy_default.yaml",
            "assets": [{"symbol_code": "EURUSD", "source_name": "dukascopy"}],
            "from": "2026-01-01",
            "to": "2026-01-02",
            "timeframe": "m1",
        }
    )

    job = create_backtest_job(request)
    stored = get_backtest_job(job.id)

    assert stored.status == "queued"
    assert stored.launch_id == job.launch_id
    assert stored.total_assets == 1
    assert stored.completed_assets == 0
