from __future__ import annotations

from typing import Any

from sqlalchemy import text

from ict.backtest.engine import BacktestEngine
from ict.strategy.blueprint_engine import StrategyBlueprintEngine
from ict.db.repositories import (
    BacktestRepository,
    CandleRepository,
    ParameterSetRepository,
    StrategyDefinitionRepository,
    StrategyRepository,
)
from ict.strategy.params import StrategyParams


def fallback_tick_size(symbol_code: str, asset_type: str | None) -> float:
    code = symbol_code.upper()
    kind = (asset_type or "").lower()
    if code == "MNQ":
        return 0.25
    if "forex" in kind:
        return 0.001 if code.endswith("JPY") else 0.00001
    if "metal" in kind:
        return 0.01
    if "crypto" in kind:
        return 0.01
    if "index" in kind:
        return 0.1
    return 0.01


def run_dataset_backtest(
    session,
    dataset,
    params: StrategyParams,
    parameter_set_name: str,
    run_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candles = CandleRepository(session).load_candles(
        dataset.symbol_id,
        dataset.source_id,
        dataset.timeframe,
        dataset.start_time,
        dataset.end_time,
    )
    strategy_version_id = StrategyRepository(session).upsert_version()
    parameter_set_id = ParameterSetRepository(session).upsert(
        name=parameter_set_name,
        params=params.model_dump(mode="json"),
        strategy_version_id=strategy_version_id,
    )
    run = BacktestRepository(session).create_run(
        strategy_version_id,
        parameter_set_id,
        dataset,
        params.execution.initial_balance,
        metadata=run_metadata,
    )
    symbol_row = session.execute(
        text("SELECT symbol_code, asset_type, tick_size FROM symbols WHERE id = :symbol_id"),
        {"symbol_id": dataset.symbol_id},
    ).mappings().one()
    tick_size = (
        float(symbol_row["tick_size"])
        if symbol_row["tick_size"] is not None
        else fallback_tick_size(symbol_row["symbol_code"], symbol_row["asset_type"])
    )
    result = BacktestEngine(params, tick_size=tick_size).run(candles)
    BacktestRepository(session).persist_result(run, result)
    return {
        "run_id": run.id,
        "dataset_id": dataset.id,
        "parameter_set_id": parameter_set_id,
        "trades": result.metrics.get("total_trades"),
        "net_profit": result.metrics.get("net_profit"),
        "profit_factor": result.metrics.get("profit_factor"),
    }


def run_dataset_strategy_definition_backtest(
    session,
    dataset,
    strategy_definition_id: str,
    parameter_set_name: str,
    run_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    definition_row = StrategyDefinitionRepository(session).require(strategy_definition_id)
    candles = CandleRepository(session).load_candles(
        dataset.symbol_id,
        dataset.source_id,
        dataset.timeframe,
        dataset.start_time,
        dataset.end_time,
    )
    strategy_version_id = StrategyRepository(session).upsert_version(
        name=definition_row.name,
        version=definition_row.version,
        source="strategy_builder",
        source_reference=definition_row.exported_path or str(definition_row.id),
        description=definition_row.description,
        metadata={"strategy_definition_id": str(definition_row.id), "status": definition_row.status},
    )
    parameter_set_id = ParameterSetRepository(session).upsert(
        name=parameter_set_name,
        params=definition_row.definition,
        strategy_version_id=strategy_version_id,
    )
    execution = definition_row.definition.get("execution", {})
    initial_balance = float(execution.get("initial_balance", 100000))
    run = BacktestRepository(session).create_run(
        strategy_version_id,
        parameter_set_id,
        dataset,
        initial_balance,
        metadata={
            **(run_metadata or {}),
            "strategy_definition_id": str(definition_row.id),
            "strategy_builder": True,
            "strategy_definition_status": definition_row.status,
        },
    )
    symbol_row = session.execute(
        text("SELECT symbol_code, asset_type, tick_size FROM symbols WHERE id = :symbol_id"),
        {"symbol_id": dataset.symbol_id},
    ).mappings().one()
    tick_size = (
        float(symbol_row["tick_size"])
        if symbol_row["tick_size"] is not None
        else fallback_tick_size(symbol_row["symbol_code"], symbol_row["asset_type"])
    )
    result = StrategyBlueprintEngine(definition_row.definition, tick_size=tick_size).run(candles)
    BacktestRepository(session).persist_result(run, result)
    return {
        "run_id": run.id,
        "dataset_id": dataset.id,
        "parameter_set_id": parameter_set_id,
        "strategy_definition_id": definition_row.id,
        "trades": result.metrics.get("total_trades"),
        "net_profit": result.metrics.get("net_profit"),
        "profit_factor": result.metrics.get("profit_factor"),
    }
