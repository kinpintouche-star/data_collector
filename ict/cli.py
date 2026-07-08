from __future__ import annotations

import subprocess
import sys
import uuid
import json
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from sqlalchemy import text

from ict.backtest.grid import expand_grid
from ict.backtest.runner import fallback_tick_size, run_dataset_backtest
from ict.core.config import get_settings
from ict.data.datasets import create_dataset, create_dataset_in_session
from ict.data.ingest import (
    ingest_market_data,
    load_csv_mapping,
    load_csv_time_unit,
    load_csv_timezone,
    provider_for_source,
)
from ict.data.mt5_client import MT5UnavailableError
from ict.db.repositories import (
    AliasRepository,
    BacktestRepository,
    DatasetRepository,
    SourceRepository,
    SymbolRepository,
    json_safe,
)
from ict.db.session import build_engine, session_scope
from ict.live.collector import collect_remote_live
from ict.live.config import load_live_sources
from ict.live.providers import discover_oanda_instruments
from ict.live.sync import prune_remote_candles, remote_storage_usage, sync_local_candles_to_remote, sync_remote_candles
from ict.strategy.params import StrategyParams, load_strategy_params

app = typer.Typer(help="ICT CRT M1 backtesting platform")
db_app = typer.Typer(help="Database commands")
sources_app = typer.Typer(help="Data source commands")
symbols_app = typer.Typer(help="Symbol and alias commands")
datasets_app = typer.Typer(help="Dataset commands")
metrics_app = typer.Typer(help="Metrics commands")
universe_app = typer.Typer(help="Collection universe commands")
live_app = typer.Typer(help="Live collector commands")

app.add_typer(db_app, name="db")
app.add_typer(sources_app, name="sources")
app.add_typer(symbols_app, name="symbols")
app.add_typer(datasets_app, name="datasets")
app.add_typer(metrics_app, name="metrics")
app.add_typer(universe_app, name="universe")
app.add_typer(live_app, name="live")

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


def _entry_sources(entry: dict) -> list[str]:
    value = entry.get("sources", entry.get("source"))
    if value is None:
        return []
    if isinstance(value, str):
        return _csv_values(value)
    return [str(item) for item in value]


def _load_collection_plan(
    universe: str | None,
    symbols: str | None,
    sources: str | None,
    group: str | None,
    limit: int | None,
) -> list[dict]:
    if symbols:
        if not sources:
            raise typer.BadParameter("Use --sources when collecting explicit --symbols.")
        rows = [
            {"symbol": symbol, "source": source, "group": "manual"}
            for symbol in _csv_values(symbols)
            for source in _csv_values(sources)
        ]
    else:
        if not universe:
            raise typer.BadParameter("Use --universe or provide --symbols and --sources.")
        with open(universe, "r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
        rows = []
        for entry in payload.get("assets", []):
            if group and entry.get("group") != group:
                continue
            for source in _entry_sources(entry):
                rows.append({"symbol": entry["symbol"], "source": source, "group": entry.get("group")})
    return rows[:limit] if limit is not None else rows


def _add_month(value: datetime) -> datetime:
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    return value.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


def _chunk_windows(start: datetime, end: datetime, chunk: str) -> list[tuple[datetime, datetime]]:
    if end < start:
        raise typer.BadParameter("--to must be greater than or equal to --from.")
    windows = []
    current = start
    while current <= end:
        if chunk == "monthly":
            next_start = _add_month(current)
        elif chunk == "daily":
            next_start = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        elif chunk == "full":
            next_start = end + timedelta(microseconds=1)
        else:
            raise typer.BadParameter("--chunk must be monthly, daily, or full.")
        chunk_end = min(end, next_start - timedelta(microseconds=1))
        windows.append((current, chunk_end))
        current = next_start
    return windows


def _coverage_for(session, symbol_code: str, source_name: str, timeframe: str) -> dict:
    row = session.execute(
        text(
            """
            SELECT
                COUNT(c.id) AS candle_rows,
                MIN(c.time_open) AS first_candle_time,
                MAX(c.time_open) AS last_candle_time,
                COUNT(c.id) FILTER (WHERE c.quality_flags <> '{}'::jsonb) AS flagged_candles
            FROM symbols s
            JOIN data_sources ds ON ds.name = :source_name
            LEFT JOIN market_candles c
                ON c.symbol_id = s.id
                AND c.source_id = ds.id
                AND c.timeframe = :timeframe
            WHERE s.symbol_code = :symbol_code
            """
        ),
        {"symbol_code": symbol_code, "source_name": source_name, "timeframe": timeframe.upper()},
    ).mappings().one()
    first = row["first_candle_time"]
    last = row["last_candle_time"]
    days = None
    if first is not None and last is not None:
        days = (last - first).total_seconds() / 86400
    return {
        "candle_rows": int(row["candle_rows"] or 0),
        "first_candle_time": first,
        "last_candle_time": last,
        "days_covered": days,
        "flagged_candles": int(row["flagged_candles"] or 0),
    }


def _plan_summary(plan: list[dict]) -> dict:
    groups: dict[str, int] = {}
    sources: dict[str, int] = {}
    for item in plan:
        groups[item.get("group") or "unknown"] = groups.get(item.get("group") or "unknown", 0) + 1
        sources[item["source"]] = sources.get(item["source"], 0) + 1
    return {
        "assets": len(plan),
        "groups": groups,
        "sources": sources,
        "forex_share": groups.get("forex", 0) / len(plan) if plan else 0,
    }


def _normalize_symbol_name(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def _symbol_suggestions(target: str, available: set[str], limit: int = 5) -> list[str]:
    target_norm = _normalize_symbol_name(target)
    scored = []
    for candidate in available:
        candidate_norm = _normalize_symbol_name(candidate)
        if not candidate_norm:
            continue
        if target_norm in candidate_norm or candidate_norm in target_norm:
            score = 0.98
        else:
            score = SequenceMatcher(None, target_norm, candidate_norm).ratio()
        if score >= 0.45:
            scored.append((score, candidate))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [candidate for _, candidate in scored[:limit]]


def _short_error(exc: Exception, limit: int = 800) -> str:
    message = str(exc).replace("\n", " ")
    return message if len(message) <= limit else message[: limit - 3] + "..."


def _fallback_tick_size(symbol_code: str, asset_type: str | None) -> float:
    return fallback_tick_size(symbol_code, asset_type)


def _print_json(payload) -> None:
    console.print_json(data=json_safe(payload))


def _remote_database_url(value: str | None) -> str:
    if value:
        return value
    import os

    env_value = os.getenv("LIVE_REMOTE_DATABASE_URL") or get_settings().live_remote_database_url
    if not env_value:
        raise typer.BadParameter("Use --remote-database-url or set LIVE_REMOTE_DATABASE_URL.")
    return env_value


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
    return run_dataset_backtest(session, dataset, params, parameter_set_name)


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


@sources_app.command("search")
def sources_search(
    source: str = typer.Option(..., "--source"),
    query: str = typer.Option(..., "--query"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    with session_scope() as session:
        data_source = SourceRepository(session).require_by_name(source)
    try:
        provider = provider_for_source(data_source.source_type)
        terms = [_normalize_symbol_name(term) for term in query.split() if term.strip()]
        rows = []
        for symbol in provider.list_symbols():
            haystack = _normalize_symbol_name(
                " ".join(
                    str(symbol.get(key) or "")
                    for key in ("source_symbol", "description", "path")
                )
            )
            if all(term in haystack for term in terms):
                rows.append(symbol)
            if len(rows) >= limit:
                break
        _print_json({"source": source, "query": query, "count": len(rows), "matches": rows})
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
    time_unit = load_csv_time_unit(mapping)
    result = ingest_market_data(
        symbol,
        source,
        timeframe,
        _parse_date(from_date),
        _parse_date(to_date),
        file_path=file,
        mapping=mapping_payload,
        time_unit=time_unit,
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
    time_unit = load_csv_time_unit(mapping)
    result = ingest_market_data(
        symbol,
        source,
        timeframe,
        _parse_date(from_date),
        _parse_date(to_date),
        file_path=file,
        mapping=mapping_payload,
        timezone=configured_timezone,
        time_unit=time_unit,
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


@universe_app.command("audit")
def universe_audit(
    universe: str = typer.Option("configs/universe_default_40.yaml", "--universe"),
    group: Optional[str] = typer.Option(None, "--group"),
    limit: Optional[int] = typer.Option(None, "--limit"),
    timeframe: str = typer.Option("M1", "--timeframe"),
    target_days: int = typer.Option(180, "--target-days"),
    check_provider: bool = typer.Option(False, "--check-provider/--no-provider-check"),
) -> None:
    plan = _load_collection_plan(universe, None, None, group, limit)
    provider_symbols_by_source: dict[str, set[str] | None] = {}
    rows = []

    with session_scope() as session:
        for item in plan:
            symbol_code = item["symbol"]
            source_name = item["source"]
            row = {
                "symbol": symbol_code,
                "source": source_name,
                "group": item.get("group"),
                "timeframe": timeframe.upper(),
                "status": "ready",
                "source_type": None,
                "source_symbol": None,
                "alias_ok": False,
                "provider_ok": None,
                "coverage": None,
                "notes": [],
            }

            try:
                source = SourceRepository(session).require_by_name(source_name)
                row["source_type"] = source.source_type
            except Exception as exc:
                row["status"] = "source_missing"
                row["notes"].append(str(exc))
                rows.append(row)
                continue

            try:
                alias = AliasRepository(session).resolve(symbol_code, source_name)
                row["source_symbol"] = alias.source_symbol
                row["alias_ok"] = True
            except Exception as exc:
                row["status"] = "alias_missing"
                row["notes"].append(str(exc))
                rows.append(row)
                continue

            row["coverage"] = _coverage_for(session, symbol_code, source_name, timeframe)
            days = row["coverage"]["days_covered"] or 0
            if days >= target_days:
                row["coverage"]["target_ok"] = True
            else:
                row["coverage"]["target_ok"] = False
                row["coverage"]["missing_days_to_target"] = round(target_days - days, 2)

            if check_provider and source.source_type == "mt5":
                if source_name not in provider_symbols_by_source:
                    try:
                        provider = provider_for_source(source.source_type)
                        provider_symbols_by_source[source_name] = {
                            str(symbol["source_symbol"]) for symbol in provider.list_symbols()
                        }
                    except Exception as exc:
                        provider_symbols_by_source[source_name] = None
                        row["provider_ok"] = False
                        row["status"] = "provider_check_failed"
                        row["notes"].append(str(exc))
                available = provider_symbols_by_source.get(source_name)
                if available is not None:
                    row["provider_ok"] = row["source_symbol"] in available
                    if not row["provider_ok"]:
                        row["status"] = "provider_symbol_missing"
                        row["suggestions"] = _symbol_suggestions(row["source_symbol"], available)
            elif source.source_type == "binance_public":
                row["provider_ok"] = True
            elif check_provider:
                row["provider_ok"] = None

            rows.append(row)

    summary = _plan_summary(plan)
    summary.update(
        {
            "ready": sum(1 for row in rows if row["status"] == "ready"),
            "alias_missing": sum(1 for row in rows if row["status"] == "alias_missing"),
            "source_missing": sum(1 for row in rows if row["status"] == "source_missing"),
            "provider_symbol_missing": sum(1 for row in rows if row["status"] == "provider_symbol_missing"),
            "provider_check_failed": sum(1 for row in rows if row["status"] == "provider_check_failed"),
            "coverage_target_ok": sum(1 for row in rows if (row.get("coverage") or {}).get("target_ok")),
            "target_days": target_days,
        }
    )
    _print_json({"summary": summary, "assets": rows})


@app.command("collect")
def collect(
    from_date: str = typer.Option(..., "--from", "--from-date"),
    to_date: str = typer.Option(..., "--to", "--to-date"),
    universe: Optional[str] = typer.Option("configs/universe_default_40.yaml", "--universe"),
    symbols: Optional[str] = typer.Option(None, "--symbols"),
    sources: Optional[str] = typer.Option(None, "--sources"),
    group: Optional[str] = typer.Option(None, "--group"),
    timeframe: str = typer.Option("M1", "--timeframe"),
    chunk: str = typer.Option("monthly", "--chunk"),
    limit: Optional[int] = typer.Option(None, "--limit"),
    create_datasets: bool = typer.Option(True, "--create-datasets/--no-datasets"),
    continue_on_error: bool = typer.Option(True, "--continue-on-error/--fail-fast"),
    missing_policy: str = typer.Option("raise", "--missing-policy"),
) -> None:
    start = _parse_date(from_date)
    end = _parse_date(to_date)
    plan = _load_collection_plan(universe, symbols, sources, group, limit)
    windows = _chunk_windows(start, end, chunk)
    summaries = []

    for item in plan:
        symbol = item["symbol"]
        source = item["source"]
        with session_scope() as session:
            source_type = SourceRepository(session).require_by_name(source).source_type
        provider_kwargs = {}
        if source_type == "binance_public":
            provider_kwargs = {
                "frequency": "daily" if chunk == "daily" else "monthly",
                "missing_policy": missing_policy,
            }

        chunk_results = []
        for chunk_start, chunk_end in windows:
            try:
                result = ingest_market_data(
                    symbol,
                    source,
                    timeframe,
                    chunk_start,
                    chunk_end,
                    **provider_kwargs,
                )
                chunk_results.append(
                    {
                        "status": "completed",
                        "from": chunk_start,
                        "to": chunk_end,
                        "rows_fetched": result["rows_fetched"],
                        "rows_inserted": result["rows_inserted"],
                        "rows_updated": result["rows_updated"],
                        "rows_skipped": result["rows_skipped"],
                    }
                )
            except Exception as exc:
                chunk_results.append(
                    {
                        "status": "failed",
                        "from": chunk_start,
                        "to": chunk_end,
                        "error": _short_error(exc),
                    }
                )
                if not continue_on_error:
                    raise

        dataset = None
        if create_datasets and any(result["status"] == "completed" for result in chunk_results):
            try:
                dataset = create_dataset(
                    symbol,
                    source,
                    timeframe,
                    start,
                    end,
                    f"{symbol}_{source}_{timeframe}_{start:%Y%m%d}_{end:%Y%m%d}",
                )
            except Exception as exc:
                dataset = {"status": "failed", "error": _short_error(exc)}

        summaries.append(
            {
                "symbol": symbol,
                "source": source,
                "group": item.get("group"),
                "chunks": len(chunk_results),
                "completed_chunks": sum(1 for result in chunk_results if result["status"] == "completed"),
                "failed_chunks": sum(1 for result in chunk_results if result["status"] == "failed"),
                "rows_fetched": sum(result.get("rows_fetched", 0) for result in chunk_results),
                "rows_inserted": sum(result.get("rows_inserted", 0) for result in chunk_results),
                "rows_updated": sum(result.get("rows_updated", 0) for result in chunk_results),
                "dataset": dataset,
                "chunk_results": chunk_results,
            }
        )

    _print_json(
        {
            "assets": len(summaries),
            "timeframe": timeframe.upper(),
            "from": start,
            "to": end,
            "chunk": chunk,
            "summaries": summaries,
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


@live_app.command("sources")
def live_sources(config: str = "configs/live_sources.yaml", enabled_only: bool = typer.Option(False, "--enabled-only")) -> None:
    rows = []
    for source in load_live_sources(config):
        if enabled_only and not source.enabled:
            continue
        rows.append(source.__dict__)
    _print_json({"count": len(rows), "sources": rows})


@live_app.command("oanda-instruments")
def live_oanda_instruments(
    config: str = typer.Option("configs/live_sources.yaml", "--config"),
    account_id: Optional[str] = typer.Option(None, "--account-id"),
    token: Optional[str] = typer.Option(None, "--token"),
    base_url: Optional[str] = typer.Option(None, "--base-url"),
    query: Optional[str] = typer.Option(None, "--query"),
) -> None:
    instruments = discover_oanda_instruments(account_id=account_id, token=token, base_url=base_url)
    available = {str(row.get("name")) for row in instruments if row.get("name")}
    by_name = {str(row.get("name")): row for row in instruments if row.get("name")}
    configured = [source for source in load_live_sources(config) if source.provider == "oanda"]
    checks = []
    for source in configured:
        instrument = source.provider_symbol or source.source_symbol
        row = by_name.get(instrument)
        checks.append(
            {
                "symbol_code": source.symbol_code,
                "configured_instrument": instrument,
                "enabled": source.enabled,
                "available": row is not None,
                "display_name": row.get("displayName") if row else None,
                "type": row.get("type") if row else None,
                "suggestions": [] if row else _symbol_suggestions(instrument, available),
            }
        )
    filtered = instruments
    if query:
        needle = query.lower()
        filtered = [
            row
            for row in instruments
            if needle in str(row.get("name", "")).lower()
            or needle in str(row.get("displayName", "")).lower()
            or needle in str(row.get("type", "")).lower()
        ]
    _print_json(
        {
            "instrument_count": len(instruments),
            "configured_oanda_sources": len(configured),
            "configured_available": sum(1 for row in checks if row["available"]),
            "configured_checks": checks,
            "instruments": filtered[:500],
        }
    )


@live_app.command("register-sources")
def live_register_sources(
    config: str = "configs/live_sources.yaml",
    remote_database_url: Optional[str] = typer.Option(None, "--remote-database-url"),
) -> None:
    engine = build_engine(remote_database_url) if remote_database_url else build_engine()
    sources = load_live_sources(config)
    rows = []
    with engine.begin() as connection:
        for source in sources:
            row = connection.execute(
                text(
                    """
                    INSERT INTO collector_source_state (
                        symbol_id,
                        source_id,
                        source_symbol,
                        provider,
                        timeframe,
                        priority,
                        enabled,
                        poll_interval_minutes,
                        retention_days,
                        collection_mode,
                        status,
                        metadata,
                        updated_at
                    )
                    SELECT
                        s.id,
                        ds.id,
                        :source_symbol,
                        :provider,
                        :timeframe,
                        :priority,
                        :enabled,
                        :poll_interval_minutes,
                        :retention_days,
                        :collection_mode,
                        'pending',
                        CAST(:metadata AS jsonb),
                        now()
                    FROM symbols s
                    JOIN data_sources ds ON ds.name = :source_name
                    WHERE s.symbol_code = :symbol_code
                    ON CONFLICT (symbol_id, source_id, timeframe)
                    DO UPDATE SET
                        source_symbol = EXCLUDED.source_symbol,
                        provider = EXCLUDED.provider,
                        priority = EXCLUDED.priority,
                        enabled = EXCLUDED.enabled,
                        poll_interval_minutes = EXCLUDED.poll_interval_minutes,
                        retention_days = EXCLUDED.retention_days,
                        collection_mode = EXCLUDED.collection_mode,
                        status = CASE
                            WHEN collector_source_state.last_success_at IS NULL THEN 'pending'
                            ELSE collector_source_state.status
                        END,
                        metadata = EXCLUDED.metadata,
                        updated_at = now()
                    RETURNING id
                    """
                ),
                {
                    "symbol_code": source.symbol_code,
                    "source_name": source.source_name,
                    "source_symbol": source.source_symbol,
                    "provider": source.provider,
                    "timeframe": source.timeframe,
                    "priority": source.priority,
                    "enabled": source.enabled,
                    "poll_interval_minutes": source.poll_interval_minutes,
                    "retention_days": source.retention_days,
                    "collection_mode": source.collection_mode,
                    "metadata": json.dumps(json_safe(
                        {
                            "provider_symbol": source.provider_symbol,
                            "fallback_provider": source.fallback_provider,
                            "fallback_provider_symbol": source.fallback_provider_symbol,
                            "dataset": source.dataset,
                            "schema": source.schema,
                            "max_cost_usd": source.max_cost_usd,
                        }
                    )),
                },
            ).scalar_one_or_none()
            rows.append({"symbol": source.symbol_code, "source": source.source_name, "registered": row is not None})
    _print_json({"count": len(rows), "registered": sum(1 for row in rows if row["registered"]), "sources": rows})


@live_app.command("status")
def live_status(
    remote_database_url: Optional[str] = typer.Option(None, "--remote-database-url"),
    stale_after_hours: int = typer.Option(36, "--stale-after-hours"),
) -> None:
    engine = build_engine(remote_database_url) if remote_database_url else build_engine()
    query = text(
        """
        SELECT
            symbol_code,
            source_name,
            source_symbol,
            provider,
            timeframe,
            priority,
            enabled,
            retention_days,
            collection_mode,
            status,
            last_candle_time,
            last_success_at,
            last_error_at,
            last_error_message,
            consecutive_failures,
            lag_seconds,
            open_incidents,
            highest_open_severity
        FROM mart_live_collector
        ORDER BY enabled DESC, priority, symbol_code, source_name
        """
    )
    with engine.connect() as connection:
        rows = [dict(row) for row in connection.execute(query).mappings()]
    stale = [
        row
        for row in rows
        if row.get("lag_seconds") is not None and int(row["lag_seconds"]) > stale_after_hours * 3600
    ]
    _print_json(
        {
            "sources": len(rows),
            "enabled": sum(1 for row in rows if row.get("enabled")),
            "stale_after_hours": stale_after_hours,
            "stale": len(stale),
            "open_incidents": sum(int(row.get("open_incidents") or 0) for row in rows),
            "rows": rows,
        }
    )


@live_app.command("incidents")
def live_incidents(
    remote_database_url: Optional[str] = typer.Option(None, "--remote-database-url"),
    open_only: bool = typer.Option(True, "--open-only/--all"),
) -> None:
    engine = build_engine(remote_database_url) if remote_database_url else build_engine()
    where = "WHERE ci.status = 'open'" if open_only else ""
    query = text(
        f"""
        SELECT
            ci.id,
            ci.incident_key,
            s.symbol_code,
            ds.name AS source_name,
            ci.timeframe,
            ci.severity,
            ci.status,
            ci.title,
            ci.message,
            ci.failure_count,
            ci.first_seen_at,
            ci.last_seen_at,
            ci.resolved_at
        FROM collector_incidents ci
        LEFT JOIN symbols s ON s.id = ci.symbol_id
        LEFT JOIN data_sources ds ON ds.id = ci.source_id
        {where}
        ORDER BY ci.status, ci.last_seen_at DESC
        LIMIT 500
        """
    )
    with engine.connect() as connection:
        rows = [dict(row) for row in connection.execute(query).mappings()]
    _print_json({"count": len(rows), "incidents": rows})


@live_app.command("sync")
def live_sync(
    from_remote: bool = typer.Option(False, "--from-remote"),
    to_remote: bool = typer.Option(False, "--to-remote"),
    remote_database_url: Optional[str] = typer.Option(None, "--remote-database-url"),
    since: Optional[str] = typer.Option(None, "--since"),
    until: Optional[str] = typer.Option(None, "--until"),
    symbols: Optional[str] = typer.Option(None, "--symbols"),
    limit: int = typer.Option(250000, "--limit"),
    chunk_days: int = typer.Option(7, "--chunk-days"),
    retention_days: int = typer.Option(30, "--retention-days"),
    config: str = typer.Option("configs/live_sources.yaml", "--config"),
) -> None:
    if from_remote == to_remote:
        raise typer.BadParameter("Use exactly one of --from-remote or --to-remote.")
    remote_url = _remote_database_url(remote_database_url)
    if from_remote:
        result = sync_remote_candles(
            remote_url,
            since=_parse_date(since) if since else None,
            until=_parse_date(until) if until else None,
            symbols=_csv_values(symbols) if symbols else None,
            limit=limit,
        )
    else:
        result = sync_local_candles_to_remote(
            remote_url,
            since=_parse_date(since) if since else None,
            until=_parse_date(until) if until else None,
            symbols=_csv_values(symbols) if symbols else None,
            limit=limit,
            chunk_days=chunk_days,
            retention_days=retention_days,
            config=config,
        )
    _print_json(result.as_dict())


@live_app.command("storage")
def live_storage(
    remote_database_url: Optional[str] = typer.Option(None, "--remote-database-url"),
) -> None:
    remote_url = _remote_database_url(remote_database_url)
    _print_json(remote_storage_usage(remote_url))


@live_app.command("prune-remote")
def live_prune_remote(
    remote_database_url: Optional[str] = typer.Option(None, "--remote-database-url"),
    older_than: Optional[str] = typer.Option(None, "--older-than"),
    retention_days: int = typer.Option(30, "--retention-days"),
    symbols: Optional[str] = typer.Option(None, "--symbols"),
    require_local: bool = typer.Option(True, "--require-local/--no-require-local"),
    execute: bool = typer.Option(False, "--execute/--dry-run"),
) -> None:
    if older_than:
        cutoff = _parse_date(older_than)
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    result = prune_remote_candles(
        _remote_database_url(remote_database_url),
        cutoff=cutoff,
        symbols=_csv_values(symbols) if symbols else None,
        require_local=require_local,
        dry_run=not execute,
    )
    _print_json(result.as_dict())


@live_app.command("collect-remote")
def live_collect_remote(
    remote_database_url: Optional[str] = typer.Option(None, "--remote-database-url"),
    since: Optional[str] = typer.Option(None, "--since"),
    until: Optional[str] = typer.Option(None, "--until"),
    symbols: Optional[str] = typer.Option(None, "--symbols"),
    config: str = typer.Option("configs/live_sources.yaml", "--config"),
    max_priority: Optional[int] = typer.Option(None, "--max-priority"),
    max_workers: int = typer.Option(4, "--max-workers"),
    upload_chunk_rows: int = typer.Option(2500, "--upload-chunk-rows"),
    submit_pause_seconds: float = typer.Option(0.25, "--submit-pause-seconds"),
    trigger_type: str = typer.Option("github_actions", "--trigger-type"),
    emit_jsonl: bool = typer.Option(False, "--emit-jsonl/--no-jsonl"),
    log_path: Optional[str] = typer.Option(None, "--log-path"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    if dry_run:
        import os

        remote_url = (
            remote_database_url
            or os.getenv("LIVE_REMOTE_DATABASE_URL")
            or get_settings().live_remote_database_url
            or "dry-run"
        )
    else:
        remote_url = _remote_database_url(remote_database_url)
    effective_log_path = log_path or ("collector-logs/live-collector.jsonl" if emit_jsonl else None)
    result = collect_remote_live(
        remote_url,
        since=_parse_date(since) if since else None,
        until=_parse_date(until) if until else None,
        symbols=_csv_values(symbols) if symbols else None,
        config=config,
        max_priority=max_priority,
        max_workers=max_workers,
        upload_chunk_rows=upload_chunk_rows,
        submit_pause_seconds=submit_pause_seconds,
        trigger_type=trigger_type,
        dry_run=dry_run,
        log_path=effective_log_path,
    )
    _print_json(result.as_dict())


@app.command("dashboard")
def dashboard() -> None:
    app_path = Path(__file__).parent / "dashboard" / "app.py"
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app_path)], check=True)


@app.command("api")
def api(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(True, "--reload/--no-reload"),
) -> None:
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "ict.api.app:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if reload:
        command.append("--reload")
    subprocess.run(command, check=True)


@app.command("web")
def web() -> None:
    web_path = Path(__file__).resolve().parents[1] / "web"
    subprocess.run(["npm", "run", "dev"], cwd=web_path, check=True)


if __name__ == "__main__":
    app()
