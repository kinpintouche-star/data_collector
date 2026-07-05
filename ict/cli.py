from __future__ import annotations

import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console

from ict.backtest.engine import BacktestEngine
from ict.backtest.grid import expand_grid
from ict.core.config import get_settings
from ict.data.datasets import create_dataset, create_dataset_in_session
from ict.data.ingest import ingest_market_data, load_csv_mapping, load_csv_timezone, provider_for_source
from ict.data.mt5_client import MT5UnavailableError
from ict.db.repositories import (
    AliasRepository,
    BacktestRepository,
    CandleRepository,
    DatasetRepository,
    ParameterSetRepository,
    SourceRepository,
    StrategyRepository,
    SymbolRepository,
    json_safe,
)
from ict.db.session import session_scope
from ict.strategy.params import StrategyParams, load_strategy_params

app = typer.Typer(help="ICT CRT M1 backtesting platform")
db_app = typer.Typer(help="Database commands")
sources_app = typer.Typer(help="Data source commands")
symbols_app = typer.Typer(help="Symbol and alias commands")
datasets_app = typer.Typer(help="Dataset commands")
metrics_app = typer.Typer(help="Metrics commands")

app.add_typer(db_app, name="db")
app.add_typer(sources_app, name="sources")
app.add_typer(symbols_app, name="symbols")
app.add_typer(datasets_app, name="datasets")
app.add_typer(metrics_app, name="metrics")

console = Console()


def _alembic_config() -> Config:
    from alembic.config import Config

    return Config(str(Path("alembic.ini").resolve()))


def _alembic_command():
    from alembic import command

    return command


def _parse_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _csv_values(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _print_json(payload) -> None:
    console.print_json(data=json_safe(payload))


def _resolve_dataset(
    session,
    dataset_id: Optional[str],
    symbol: Optional[str],
    source: Optional[str],
    from_date: Optional[str],
    to_date: Optional[str],
    timeframe: str = "M1",
):
    if dataset_id:
        return DatasetRepository(session).require(uuid.UUID(dataset_id))
    if not (symbol and source and from_date and to_date):
        raise typer.BadParameter("Use --dataset-id or provide --symbol, --source, --from, and --to.")
    created = create_dataset_in_session(
        session,
        symbol,
        source,
        timeframe,
        _parse_date(from_date),
        _parse_date(to_date),
    )
    return DatasetRepository(session).require(created["dataset_id"])


def _run_dataset_backtest(session, dataset, params: StrategyParams, parameter_set_name: str) -> dict:
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
    )
    result = BacktestEngine(params).run(candles)
    BacktestRepository(session).persist_result(run, result)
    return {
        "run_id": run.id,
        "dataset_id": dataset.id,
        "parameter_set_id": parameter_set_id,
        "trades": result.metrics.get("total_trades"),
        "net_profit": result.metrics.get("net_profit"),
        "profit_factor": result.metrics.get("profit_factor"),
    }


@db_app.command("upgrade")
def db_upgrade(revision: str = "head") -> None:
    _alembic_command().upgrade(_alembic_config(), revision)
    console.print("[green]Database upgraded.[/green]")


@db_app.command("downgrade")
def db_downgrade(revision: str = "-1") -> None:
    _alembic_command().downgrade(_alembic_config(), revision)
    console.print("[yellow]Database downgraded.[/yellow]")


@db_app.command("current")
def db_current() -> None:
    _alembic_command().current(_alembic_config())


@db_app.command("seed-defaults")
def db_seed_defaults() -> None:
    sources_sync()
    symbols_sync()


@db_app.command("refresh-views")
def db_refresh_views() -> None:
    from ict.analytics.marts import refresh_views

    with session_scope() as session:
        refresh_views(session)
    console.print("[green]Analytics views refreshed.[/green]")


@sources_app.command("sync")
def sources_sync(config: str = "configs/sources.yaml") -> None:
    with open(config, "r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    rows = []
    with session_scope() as session:
        repo = SourceRepository(session)
        for source in payload.get("sources", []):
            values = {**source, "config": source.get("config") or {}}
            source_id = repo.upsert_source(values)
            rows.append({"name": source["name"], "id": source_id})
    _print_json(rows)


@sources_app.command("list")
def sources_list() -> None:
    with session_scope() as session:
        rows = [
            {
                "name": source.name,
                "type": source.source_type,
                "active": source.is_active,
                "priority": source.priority,
            }
            for source in SourceRepository(session).list_sources()
        ]
        _print_json(rows)


@sources_app.command("test")
def sources_test(source: str = typer.Option(..., "--source")) -> None:
    with session_scope() as session:
        data_source = SourceRepository(session).require_by_name(source)
    try:
        provider = provider_for_source(data_source.source_type)
        symbols = provider.list_symbols()
        _print_json({"source": source, "symbols_sample": symbols[:5], "count": len(symbols)})
    except MT5UnavailableError as exc:
        console.print(str(exc), style="red")
        raise typer.Exit(1)


@symbols_app.command("sync")
def symbols_sync(config: str = "configs/symbols.yaml") -> None:
    with open(config, "r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    rows = []
    with session_scope() as session:
        symbols = SymbolRepository(session)
        sources = SourceRepository(session)
        aliases = AliasRepository(session)
        for symbol in payload.get("symbols", []):
            alias_payloads = symbol.pop("aliases", [])
            values = {**symbol, "metadata_": symbol.get("metadata", {})}
            values.pop("metadata", None)
            symbol_id = symbols.upsert_symbol(values)
            for alias in alias_payloads:
                source = sources.require_by_name(alias["source"])
                alias_values = {
                    "symbol_id": symbol_id,
                    "source_id": source.id,
                    "source_symbol": alias["source_symbol"],
                    "source_exchange": alias.get("source_exchange"),
                    "source_asset_type": alias.get("source_asset_type"),
                    "source_timezone": alias.get("source_timezone"),
                    "min_timeframe": alias.get("min_timeframe"),
                    "max_timeframe": alias.get("max_timeframe"),
                    "price_multiplier": alias.get("price_multiplier", 1),
                    "tick_size_override": alias.get("tick_size_override"),
                    "point_size_override": alias.get("point_size_override"),
                    "metadata_": alias.get("metadata", {}),
                }
                aliases.upsert_alias(alias_values)
            rows.append({"symbol_code": symbol["symbol_code"], "id": symbol_id, "aliases": len(alias_payloads)})
    _print_json(rows)


@symbols_app.command("list")
def symbols_list() -> None:
    with session_scope() as session:
        rows = [
            {"symbol_code": symbol.symbol_code, "asset_type": symbol.asset_type, "active": symbol.is_active}
            for symbol in SymbolRepository(session).list_symbols()
        ]
    _print_json(rows)


@symbols_app.command("aliases")
def symbols_aliases(symbol: str = typer.Option(..., "--symbol")) -> None:
    with session_scope() as session:
        rows = []
        for alias in AliasRepository(session).list_for_symbol(symbol):
            rows.append(
                {
                    "source": alias.source.name,
                    "source_symbol": alias.source_symbol,
                    "timezone": alias.source_timezone,
                    "active": alias.is_active,
                }
            )
    _print_json(rows)


@app.command("ingest")
def ingest(
    symbol: str = typer.Option(..., "--symbol"),
    source: str = typer.Option(..., "--source"),
    timeframe: str = typer.Option("M1", "--timeframe"),
    from_date: str = typer.Option(..., "--from", "--from-date"),
    to_date: str = typer.Option(..., "--to", "--to-date"),
    file: Optional[str] = typer.Option(None, "--file"),
    mapping: Optional[str] = typer.Option(None, "--mapping"),
) -> None:
    mapping_payload = load_csv_mapping(mapping)
    result = ingest_market_data(
        symbol,
        source,
        timeframe,
        _parse_date(from_date),
        _parse_date(to_date),
        file_path=file,
        mapping=mapping_payload,
    )
    _print_json(result)


@app.command("ingest-csv")
def ingest_csv(
    symbol: str = typer.Option(..., "--symbol"),
    source: str = typer.Option("csv", "--source"),
    file: str = typer.Option(..., "--file"),
    timeframe: str = typer.Option("M1", "--timeframe"),
    from_date: str = typer.Option(..., "--from", "--from-date"),
    to_date: str = typer.Option(..., "--to", "--to-date"),
    timezone_name: str = typer.Option("UTC", "--timezone"),
    mapping: Optional[str] = typer.Option(None, "--mapping"),
) -> None:
    mapping_payload = load_csv_mapping(mapping)
    configured_timezone = load_csv_timezone(mapping) or timezone_name
    result = ingest_market_data(
        symbol,
        source,
        timeframe,
        _parse_date(from_date),
        _parse_date(to_date),
        file_path=file,
        mapping=mapping_payload,
        timezone=configured_timezone,
    )
    _print_json(result)


@datasets_app.command("create")
def datasets_create(
    symbol: str = typer.Option(..., "--symbol"),
    source: str = typer.Option(..., "--source"),
    timeframe: str = typer.Option("M1", "--timeframe"),
    from_date: str = typer.Option(..., "--from", "--from-date"),
    to_date: str = typer.Option(..., "--to", "--to-date"),
    name: Optional[str] = typer.Option(None, "--name"),
    version: str = typer.Option("1", "--version"),
) -> None:
    result = create_dataset(
        symbol,
        source,
        timeframe,
        _parse_date(from_date),
        _parse_date(to_date),
        name,
        version,
    )
    _print_json(result)


@datasets_app.command("list")
def datasets_list(symbol: Optional[str] = typer.Option(None, "--symbol")) -> None:
    with session_scope() as session:
        symbol_id = SymbolRepository(session).require_by_code(symbol).id if symbol else None
        rows = []
        for dataset in DatasetRepository(session).list(symbol_id):
            rows.append(
                {
                    "id": dataset.id,
                    "symbol_id": dataset.symbol_id,
                    "source_id": dataset.source_id,
                    "timeframe": dataset.timeframe,
                    "start": dataset.start_time,
                    "end": dataset.end_time,
                    "candles": dataset.candles_count,
                    "quality": dataset.quality_score,
                }
            )
    _print_json(rows)


@datasets_app.command("quality")
def datasets_quality(dataset_id: str = typer.Option(..., "--dataset-id")) -> None:
    with session_scope() as session:
        dataset = DatasetRepository(session).require(uuid.UUID(dataset_id))
        _print_json(
            {
                "dataset_id": dataset.id,
                "candles": dataset.candles_count,
                "missing": dataset.missing_candles_count,
                "duplicates": dataset.duplicate_candles_count,
                "quality_score": dataset.quality_score,
                "checksum": dataset.checksum,
                "metadata": dataset.metadata_,
            }
        )


@app.command("backtest")
def backtest(
    dataset_id: Optional[str] = typer.Option(None, "--dataset-id"),
    symbol: Optional[str] = typer.Option(None, "--symbol"),
    source: Optional[str] = typer.Option(None, "--source"),
    from_date: Optional[str] = typer.Option(None, "--from", "--from-date"),
    to_date: Optional[str] = typer.Option(None, "--to", "--to-date"),
    config: str = typer.Option("configs/strategy_default.yaml", "--config"),
) -> None:
    params = load_strategy_params(config)
    with session_scope() as session:
        dataset = _resolve_dataset(session, dataset_id, symbol, source, from_date, to_date)
        summary = _run_dataset_backtest(session, dataset, params, Path(config).stem)
        _print_json(summary)


@app.command("grid")
def grid(
    symbols: str = typer.Option(..., "--symbols"),
    sources: str = typer.Option(..., "--sources"),
    from_date: str = typer.Option(..., "--from", "--from-date"),
    to_date: str = typer.Option(..., "--to", "--to-date"),
    grid: str = typer.Option("configs/grid_example.yaml", "--grid"),
    timeframe: str = typer.Option("M1", "--timeframe"),
    limit: Optional[int] = typer.Option(None, "--limit"),
) -> None:
    parameter_sets = expand_grid(grid)
    if limit is not None:
        parameter_sets = parameter_sets[:limit]
    symbol_list = _csv_values(symbols)
    source_list = _csv_values(sources)
    summaries = []
    with session_scope() as session:
        for symbol_code in symbol_list:
            for source_name in source_list:
                dataset = _resolve_dataset(session, None, symbol_code, source_name, from_date, to_date, timeframe)
                for index, payload in enumerate(parameter_sets, start=1):
                    params = StrategyParams.model_validate(payload)
                    name = f"{Path(grid).stem}_{symbol_code}_{source_name}_{index}"
                    summaries.append(_run_dataset_backtest(session, dataset, params, name))
                    _print_json(
                        {
                            "completed": len(summaries),
                            "symbol": symbol_code,
                            "source": source_name,
                            "parameter_set": index,
                            "run_id": summaries[-1]["run_id"],
                            "trades": summaries[-1]["trades"],
                            "net_profit": summaries[-1]["net_profit"],
                        }
                    )
    _print_json({"grid_runs": len(summaries), "symbols": symbol_list, "sources": source_list})


@metrics_app.command("refresh")
def metrics_refresh(run_id: Optional[str] = typer.Option(None, "--run-id")) -> None:
    with session_scope() as session:
        repo = BacktestRepository(session)
        if run_id:
            metrics = repo.refresh_metrics(uuid.UUID(run_id))
            _print_json({"run_id": run_id, "metrics": metrics})
        else:
            summaries = repo.refresh_all_metrics()
            _print_json({"refreshed_runs": len(summaries)})


@metrics_app.command("refresh-all")
def metrics_refresh_all() -> None:
    metrics_refresh(None)


@app.command("dashboard")
def dashboard() -> None:
    app_path = Path(__file__).parent / "dashboard" / "app.py"
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app_path)], check=True)


if __name__ == "__main__":
    app()
