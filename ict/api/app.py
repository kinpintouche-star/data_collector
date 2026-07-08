from __future__ import annotations

import threading

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from ict.api.analytics import fetch_run_analytics
from ict.api.backtests import (
    BacktestLaunchRequest,
    create_backtest_job,
    fetch_run_group_analytics,
    fetch_run_groups,
    get_backtest_job,
    list_backtest_options,
    run_backtest_job,
)
from ict.api.data_management import (
    DataFetchJobRequest,
    create_data_fetch_job,
    fetch_data_api_usage,
    fetch_data_coverage,
    get_data_fetch_job,
    run_data_fetch_job,
)
from ict.api.review import ReviewNotFoundError, build_trade_review, fetch_run_trades, fetch_runs
from ict.api.strategy_builder import (
    create_strategy_definition,
    delete_strategy_definition_record,
    export_strategy_definition_record,
    get_strategy_builder_catalog,
    get_strategy_definition,
    list_strategy_definitions,
    update_strategy_definition,
    validate_strategy_definition_record,
)
from ict.strategy.builder import StrategyDefinitionCreate, StrategyDefinitionUpdate


app = FastAPI(title="ICT Trading Lab API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/runs")
def runs(limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    return fetch_runs(limit=limit)


@app.get("/api/backtest/options")
def backtest_options() -> dict:
    try:
        return list_backtest_options()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/strategy-builder/catalog")
def strategy_builder_catalog() -> dict:
    return get_strategy_builder_catalog()


@app.get("/api/data/coverage")
def data_coverage() -> dict:
    return fetch_data_coverage()


@app.get("/api/data/api-usage")
def data_api_usage() -> dict:
    return fetch_data_api_usage()


@app.post("/api/data/fetch-jobs")
def data_fetch_job(payload: DataFetchJobRequest) -> dict:
    try:
        job = create_data_fetch_job(payload)
        thread = threading.Thread(target=run_data_fetch_job, args=(job.id,), daemon=True)
        thread.start()
        return job.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/data/fetch-jobs/{job_id}")
def data_fetch_job_status(job_id: str) -> dict:
    try:
        return get_data_fetch_job(job_id).model_dump()
    except ReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/strategy-builder/strategies")
def strategy_builder_strategies() -> list[dict]:
    return list_strategy_definitions()


@app.post("/api/strategy-builder/strategies")
def strategy_builder_create(payload: StrategyDefinitionCreate) -> dict:
    try:
        return create_strategy_definition(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/strategy-builder/strategies/{strategy_id}")
def strategy_builder_get(strategy_id: str) -> dict:
    try:
        return get_strategy_definition(strategy_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/strategy-builder/strategies/{strategy_id}")
def strategy_builder_update(strategy_id: str, payload: StrategyDefinitionUpdate) -> dict:
    try:
        return update_strategy_definition(strategy_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/strategy-builder/strategies/{strategy_id}/validate")
def strategy_builder_validate(strategy_id: str) -> dict:
    try:
        return validate_strategy_definition_record(strategy_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/strategy-builder/strategies/{strategy_id}/export")
def strategy_builder_export(strategy_id: str) -> dict:
    try:
        return export_strategy_definition_record(strategy_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/strategy-builder/strategies/{strategy_id}")
def strategy_builder_delete(strategy_id: str) -> dict:
    try:
        return delete_strategy_definition_record(strategy_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/backtest/jobs")
def backtest_job(payload: BacktestLaunchRequest) -> dict:
    try:
        job = create_backtest_job(payload)
        thread = threading.Thread(target=run_backtest_job, args=(job.id,), daemon=True)
        thread.start()
        return job.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/backtest/jobs/{job_id}")
def backtest_job_status(job_id: str) -> dict:
    try:
        return get_backtest_job(job_id).model_dump()
    except ReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/run-groups")
def run_groups(limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    return fetch_run_groups(limit=limit)


@app.get("/api/run-groups/{group_id}/analytics")
def run_group_analytics(group_id: str, symbols: str | None = Query(default=None)) -> dict:
    try:
        selected_symbols = [item.strip() for item in symbols.split(",") if item.strip()] if symbols else None
        return fetch_run_group_analytics(group_id, selected_symbols)
    except ReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/trades")
def run_trades(run_id: str) -> list[dict]:
    try:
        return fetch_run_trades(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/analytics")
def run_analytics(run_id: str) -> dict:
    try:
        return fetch_run_analytics(run_id)
    except ReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/trades/{trade_id}/review")
def trade_review(trade_id: str) -> dict:
    try:
        return build_trade_review(trade_id)
    except ReviewNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
