from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ict.db.models import (
    BacktestRun,
    DataImportJob,
    DataSource,
    Dataset,
    EquityCurve,
    Fill,
    MarketCandle,
    Order,
    ParameterSet,
    RunMetric,
    SetupEvent,
    StrategyVersion,
    Symbol,
    SymbolAlias,
    Trade,
)
from ict.backtest.metrics import summarize_trades


def stable_params_hash(params: dict[str, Any]) -> str:
    payload = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def decimalize(value: Any) -> Decimal | None:
    if value is None or pd.isna(value):
        return None
    return Decimal(str(value))


def json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            return json_safe(value.item())
        except (TypeError, ValueError):
            pass
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def db_insert_values(values: dict[str, Any]) -> dict[str, Any]:
    """Map ORM-safe attribute names to physical table column names for Core inserts."""

    out = dict(values)
    if "metadata_" in out:
        out["metadata"] = out.pop("metadata_")
    for json_key in ("metadata", "config", "quality_report", "source_params", "params", "metrics"):
        if json_key in out:
            out[json_key] = json_safe(out[json_key])
    return out


class SourceRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_name(self, name: str) -> DataSource | None:
        return self.session.scalar(select(DataSource).where(DataSource.name == name))

    def require_by_name(self, name: str) -> DataSource:
        source = self.get_by_name(name)
        if source is None:
            raise ValueError(f"Data source not found: {name}. Run `ict sources sync` first.")
        return source

    def upsert_source(self, values: dict[str, Any]) -> uuid.UUID:
        values = db_insert_values(values)
        table = DataSource.__table__
        stmt = insert(table).values(**values)
        update_values = {
            key: stmt.excluded[key]
            for key in values
            if key not in {"id", "name", "created_at"}
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=[table.c.name],
            set_=update_values,
        ).returning(table.c.id)
        return self.session.execute(stmt).scalar_one()

    def list_sources(self) -> list[DataSource]:
        return list(self.session.scalars(select(DataSource).order_by(DataSource.priority, DataSource.name)))


class SymbolRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_code(self, symbol_code: str) -> Symbol | None:
        return self.session.scalar(select(Symbol).where(Symbol.symbol_code == symbol_code))

    def require_by_code(self, symbol_code: str) -> Symbol:
        symbol = self.get_by_code(symbol_code)
        if symbol is None:
            raise ValueError(f"Symbol not found: {symbol_code}. Run `ict symbols sync` first.")
        return symbol

    def upsert_symbol(self, values: dict[str, Any]) -> uuid.UUID:
        values = db_insert_values(values)
        table = Symbol.__table__
        stmt = insert(table).values(**values)
        update_values = {
            key: stmt.excluded[key]
            for key in values
            if key not in {"id", "symbol_code", "created_at"}
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=[table.c.symbol_code],
            set_=update_values,
        ).returning(table.c.id)
        return self.session.execute(stmt).scalar_one()

    def list_symbols(self) -> list[Symbol]:
        return list(self.session.scalars(select(Symbol).order_by(Symbol.symbol_code)))


class AliasRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_alias(self, values: dict[str, Any]) -> uuid.UUID:
        values = db_insert_values(values)
        table = SymbolAlias.__table__
        stmt = insert(table).values(**values)
        update_values = {
            key: stmt.excluded[key]
            for key in values
            if key not in {"id", "symbol_id", "source_id", "source_symbol", "created_at"}
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=[table.c.symbol_id, table.c.source_id, table.c.source_symbol],
            set_=update_values,
        ).returning(table.c.id)
        return self.session.execute(stmt).scalar_one()

    def resolve(self, symbol_code: str, source_name: str) -> SymbolAlias:
        query = (
            select(SymbolAlias)
            .join(Symbol)
            .join(DataSource)
            .where(Symbol.symbol_code == symbol_code)
            .where(DataSource.name == source_name)
            .where(SymbolAlias.is_active.is_(True))
            .order_by(SymbolAlias.created_at.desc())
        )
        alias = self.session.scalar(query)
        if alias is None:
            raise ValueError(f"No alias for symbol={symbol_code}, source={source_name}.")
        return alias

    def list_for_symbol(self, symbol_code: str) -> list[SymbolAlias]:
        query = (
            select(SymbolAlias)
            .join(Symbol)
            .where(Symbol.symbol_code == symbol_code)
            .order_by(SymbolAlias.source_id, SymbolAlias.source_symbol)
        )
        return list(self.session.scalars(query))


class CandleRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_candles(self, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        table = MarketCandle.__table__
        stmt = insert(table).values(rows)
        excluded = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            index_elements=[table.c.symbol_id, table.c.source_id, table.c.timeframe, table.c.time_open],
            set_={
                "open": excluded.open,
                "high": excluded.high,
                "low": excluded.low,
                "close": excluded.close,
                "tick_volume": excluded.tick_volume,
                "real_volume": excluded.real_volume,
                "spread": excluded.spread,
                "source_symbol": excluded.source_symbol,
                "quality_flags": excluded.quality_flags,
                "metadata": excluded["metadata"],
                "ingested_at": func.now(),
            },
        )
        result = self.session.execute(stmt)
        rowcount = result.rowcount
        return rowcount if rowcount is not None and rowcount >= 0 else len(rows)

    def count_existing_candles(
        self,
        symbol_id: uuid.UUID,
        source_id: uuid.UUID,
        timeframe: str,
        time_opens: Iterable[datetime],
    ) -> int:
        times = list(time_opens)
        if not times:
            return 0
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(MarketCandle)
                .where(MarketCandle.symbol_id == symbol_id)
                .where(MarketCandle.source_id == source_id)
                .where(MarketCandle.timeframe == timeframe.upper())
                .where(MarketCandle.time_open.in_(times))
            )
            or 0
        )

    def load_candles(
        self,
        symbol_id: uuid.UUID,
        source_id: uuid.UUID,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        query = (
            select(MarketCandle)
            .where(MarketCandle.symbol_id == symbol_id)
            .where(MarketCandle.source_id == source_id)
            .where(MarketCandle.timeframe == timeframe.upper())
            .where(MarketCandle.time_open >= start)
            .where(MarketCandle.time_open <= end)
            .order_by(MarketCandle.time_open)
        )
        records = []
        for candle in self.session.scalars(query):
            records.append(
                {
                    "time_open": candle.time_open,
                    "open": float(candle.open),
                    "high": float(candle.high),
                    "low": float(candle.low),
                    "close": float(candle.close),
                    "tick_volume": candle.tick_volume,
                    "spread": float(candle.spread) if candle.spread is not None else None,
                    "real_volume": candle.real_volume,
                    "source_symbol": candle.source_symbol,
                }
            )
        return pd.DataFrame.from_records(records)

    def rows_for_frame(
        self,
        symbol_id: uuid.UUID,
        source_id: uuid.UUID,
        source_symbol: str,
        timeframe: str,
        frame: pd.DataFrame,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for record in frame.to_dict(orient="records"):
            rows.append(
                {
                    "symbol_id": symbol_id,
                    "source_id": source_id,
                    "timeframe": timeframe.upper(),
                    "time_open": pd.Timestamp(record["time_open"]).to_pydatetime(),
                    "open": decimalize(record["open"]),
                    "high": decimalize(record["high"]),
                    "low": decimalize(record["low"]),
                    "close": decimalize(record["close"]),
                    "tick_volume": int(record["tick_volume"]) if not pd.isna(record.get("tick_volume")) else None,
                    "real_volume": int(record["real_volume"]) if not pd.isna(record.get("real_volume")) else None,
                    "spread": decimalize(record.get("spread")),
                    "source_symbol": source_symbol,
                    "quality_flags": json_safe(record.get("quality_flags") or {}),
                    "metadata": json_safe(record.get("source_metadata") or {}),
                }
            )
        return rows


class ImportJobRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        source_id: uuid.UUID,
        symbol_id: uuid.UUID,
        alias_id: uuid.UUID | None,
        timeframe: str,
        start: datetime,
        end: datetime,
        source_params: dict[str, Any] | None = None,
    ) -> DataImportJob:
        job = DataImportJob(
            source_id=source_id,
            symbol_id=symbol_id,
            alias_id=alias_id,
            timeframe=timeframe.upper(),
            requested_start=start,
            requested_end=end,
            source_params=source_params or {},
            status="created",
        )
        self.session.add(job)
        self.session.flush()
        return job


class DatasetRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_dataset(
        self,
        symbol_id: uuid.UUID,
        source_id: uuid.UUID,
        timeframe: str,
        start: datetime,
        end: datetime,
        dataset_name: str | None,
        stats: dict[str, Any],
        dataset_version: str = "1",
    ) -> uuid.UUID:
        values = {
            "symbol_id": symbol_id,
            "source_id": source_id,
            "timeframe": timeframe.upper(),
            "start_time": start,
            "end_time": end,
            "dataset_name": dataset_name,
            "dataset_version": dataset_version,
            "candles_count": stats.get("candles_count"),
            "missing_candles_count": stats.get("missing_candles_count"),
            "duplicate_candles_count": stats.get("duplicate_candles_count"),
            "quality_score": decimalize(stats.get("quality_score")),
            "checksum": stats.get("checksum"),
            "status": stats.get("status", "ready"),
            "metadata": json_safe(stats.get("metadata", {})),
        }
        table = Dataset.__table__
        stmt = insert(table).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                table.c.symbol_id,
                table.c.source_id,
                table.c.timeframe,
                table.c.start_time,
                table.c.end_time,
                table.c.dataset_version,
            ],
            set_={
                "dataset_name": stmt.excluded.dataset_name,
                "candles_count": stmt.excluded.candles_count,
                "missing_candles_count": stmt.excluded.missing_candles_count,
                "duplicate_candles_count": stmt.excluded.duplicate_candles_count,
                "quality_score": stmt.excluded.quality_score,
                "checksum": stmt.excluded.checksum,
                "status": stmt.excluded.status,
                "metadata": stmt.excluded["metadata"],
            },
        ).returning(table.c.id)
        return self.session.execute(stmt).scalar_one()

    def get(self, dataset_id: uuid.UUID) -> Dataset | None:
        return self.session.get(Dataset, dataset_id)

    def require(self, dataset_id: uuid.UUID) -> Dataset:
        dataset = self.get(dataset_id)
        if dataset is None:
            raise ValueError(f"Dataset not found: {dataset_id}")
        return dataset

    def list(self, symbol_id: uuid.UUID | None = None) -> list[Dataset]:
        query = select(Dataset).order_by(Dataset.created_at.desc())
        if symbol_id is not None:
            query = query.where(Dataset.symbol_id == symbol_id)
        return list(self.session.scalars(query))


class StrategyRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_version(
        self,
        name: str = "ICT_CRT_M1",
        version: str = "python-v1.6",
        source_reference: str = "ICT_CRT_M1_Strategy_v1_6_Clean.pine",
    ) -> uuid.UUID:
        values = {
            "name": name,
            "version": version,
            "source": "python",
            "source_reference": source_reference,
            "metadata": {},
        }
        table = StrategyVersion.__table__
        stmt = insert(table).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[table.c.name, table.c.version],
            set_={"source_reference": source_reference},
        ).returning(table.c.id)
        return self.session.execute(stmt).scalar_one()


class ParameterSetRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert(self, name: str, params: dict[str, Any], strategy_version_id: uuid.UUID | None) -> uuid.UUID:
        params_hash = stable_params_hash(params)
        table = ParameterSet.__table__
        stmt = insert(table).values(
            name=name,
            params=params,
            params_hash=params_hash,
            strategy_version_id=strategy_version_id,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[table.c.params_hash],
            set_={"name": name, "params": params, "strategy_version_id": strategy_version_id},
        ).returning(table.c.id)
        return self.session.execute(stmt).scalar_one()


class BacktestRepository:
    def __init__(self, session: Session):
        self.session = session

    def create_run(
        self,
        strategy_version_id: uuid.UUID,
        parameter_set_id: uuid.UUID,
        dataset: Dataset,
        initial_balance: float,
    ) -> BacktestRun:
        run = BacktestRun(
            strategy_version_id=strategy_version_id,
            parameter_set_id=parameter_set_id,
            dataset_id=dataset.id,
            symbol_id=dataset.symbol_id,
            source_id=dataset.source_id,
            start_time=dataset.start_time,
            end_time=dataset.end_time,
            initial_balance=decimalize(initial_balance),
            status="running",
            metadata_={},
        )
        self.session.add(run)
        self.session.flush()
        return run

    def persist_result(self, run: BacktestRun, result: Any) -> None:
        for event in result.events:
            if event.get("event_time") is None:
                continue
            self.session.add(
                SetupEvent(
                    run_id=run.id,
                    dataset_id=run.dataset_id,
                    symbol_id=run.symbol_id,
                    source_id=run.source_id,
                    setup_id=event["setup_id"],
                    event_type=event["event_type"],
                    event_time=pd.Timestamp(event["event_time"]).to_pydatetime(),
                    direction=event.get("direction"),
                    price=decimalize(event.get("price")),
                    state_before=event.get("state_before"),
                    state_after=event.get("state_after"),
                    metadata_=json_safe(event.get("metadata") or {}),
                )
            )

        order_ids_by_ref: dict[str, uuid.UUID] = {}
        orders = getattr(result, "orders", pd.DataFrame())
        if not orders.empty:
            for record in orders.to_dict(orient="records"):
                order = Order(
                    run_id=run.id,
                    dataset_id=run.dataset_id,
                    symbol_id=run.symbol_id,
                    source_id=run.source_id,
                    setup_id=record.get("setup_id"),
                    order_type=record["order_type"],
                    direction=record["direction"],
                    requested_time=pd.Timestamp(record["requested_time"]).to_pydatetime(),
                    requested_price=decimalize(record.get("requested_price")),
                    volume=decimalize(record["volume"]),
                    sl=decimalize(record.get("sl")),
                    tp=decimalize(record.get("tp")),
                    status=record["status"],
                    external_order_id=record.get("external_order_id"),
                    metadata_=json_safe(record.get("metadata") or {}),
                )
                self.session.add(order)
                self.session.flush()
                order_ref = record.get("order_ref")
                if order_ref:
                    order_ids_by_ref[order_ref] = order.id

        fills = getattr(result, "fills", pd.DataFrame())
        if not fills.empty:
            for record in fills.to_dict(orient="records"):
                self.session.add(
                    Fill(
                        run_id=run.id,
                        dataset_id=run.dataset_id,
                        order_id=order_ids_by_ref.get(record.get("order_ref")),
                        symbol_id=run.symbol_id,
                        source_id=run.source_id,
                        fill_time=pd.Timestamp(record["fill_time"]).to_pydatetime(),
                        fill_price=decimalize(record["fill_price"]),
                        volume=decimalize(record["volume"]),
                        commission=decimalize(record.get("commission")) or Decimal("0"),
                        slippage=decimalize(record.get("slippage")) or Decimal("0"),
                        metadata_=json_safe(record.get("metadata") or {}),
                    )
                )

        if not result.trades.empty:
            for record in result.trades.to_dict(orient="records"):
                self.session.add(
                    Trade(
                        run_id=run.id,
                        dataset_id=run.dataset_id,
                        symbol_id=run.symbol_id,
                        source_id=run.source_id,
                        setup_id=record["setup_id"],
                        direction=record["direction"],
                        entry_time=pd.Timestamp(record["entry_time"]).to_pydatetime(),
                        entry_price=decimalize(record["entry_price"]),
                        exit_time=pd.Timestamp(record["exit_time"]).to_pydatetime()
                        if not pd.isna(record.get("exit_time"))
                        else None,
                        exit_price=decimalize(record.get("exit_price")),
                        volume=decimalize(record["volume"]),
                        sl=decimalize(record["sl"]),
                        tp=decimalize(record["tp"]),
                        exit_reason=record.get("exit_reason"),
                        pnl=decimalize(record.get("pnl")),
                        pnl_points=decimalize(record.get("pnl_points")),
                        rr=decimalize(record.get("rr")),
                        pd_type=record.get("pd_type"),
                        strategy_mode=record.get("strategy_mode"),
                        session_name=record.get("session_name"),
                        metadata_=json_safe(record.get("metadata") or {}),
                    )
                )

        if not result.equity_curve.empty:
            for record in result.equity_curve.to_dict(orient="records"):
                self.session.add(
                    EquityCurve(
                        run_id=run.id,
                        dataset_id=run.dataset_id,
                        time=pd.Timestamp(record["time"]).to_pydatetime(),
                        balance=decimalize(record["balance"]),
                        equity=decimalize(record["equity"]),
                        drawdown_abs=decimalize(record.get("drawdown_abs")),
                        drawdown_pct=decimalize(record.get("drawdown_pct")),
                        open_positions=int(record.get("open_positions") or 0),
                    )
                )

        metrics = result.metrics
        self.session.merge(
            RunMetric(
                run_id=run.id,
                dataset_id=run.dataset_id,
                total_h1_signals=int(metrics.get("total_h1_signals") or 0),
                total_setups=int(metrics.get("total_setups") or 0),
                total_legs=int(metrics.get("total_legs") or 0),
                total_pd_selected=int(metrics.get("total_pd_selected") or 0),
                total_pd_touched=int(metrics.get("total_pd_touched") or 0),
                total_rejections=int(metrics.get("total_rejections") or 0),
                total_risk_rejected=int(metrics.get("total_risk_rejected") or 0),
                total_trades=int(metrics.get("total_trades") or 0),
                total_wins=int(metrics.get("total_wins") or 0),
                total_losses=int(metrics.get("total_losses") or 0),
                winrate=decimalize(metrics.get("winrate")),
                avg_rr=decimalize(metrics.get("avg_rr")),
                median_rr=decimalize(metrics.get("median_rr")),
                profit_factor=decimalize(metrics.get("profit_factor")),
                expectancy=decimalize(metrics.get("expectancy")),
                net_profit=decimalize(metrics.get("net_profit")),
                max_drawdown_abs=decimalize(metrics.get("max_drawdown_abs")),
                max_drawdown_pct=decimalize(metrics.get("max_drawdown_pct")),
                max_consecutive_losses=metrics.get("max_consecutive_losses"),
                avg_trade_duration_seconds=decimalize(metrics.get("avg_trade_duration_seconds")),
                metrics=json_safe(metrics),
            )
        )
        run.status = "completed"
        run.final_balance = decimalize(
            result.equity_curve["balance"].iloc[-1] if not result.equity_curve.empty else run.initial_balance
        )

    def refresh_metrics(self, run_id: uuid.UUID) -> dict[str, Any]:
        run = self.session.get(BacktestRun, run_id)
        if run is None:
            raise ValueError(f"Backtest run not found: {run_id}")
        metrics = self._calculate_metrics(run)
        self.session.merge(
            RunMetric(
                run_id=run.id,
                dataset_id=run.dataset_id,
                total_h1_signals=int(metrics.get("total_h1_signals") or 0),
                total_setups=int(metrics.get("total_setups") or 0),
                total_legs=int(metrics.get("total_legs") or 0),
                total_pd_selected=int(metrics.get("total_pd_selected") or 0),
                total_pd_touched=int(metrics.get("total_pd_touched") or 0),
                total_rejections=int(metrics.get("total_rejections") or 0),
                total_risk_rejected=int(metrics.get("total_risk_rejected") or 0),
                total_trades=int(metrics.get("total_trades") or 0),
                total_wins=int(metrics.get("total_wins") or 0),
                total_losses=int(metrics.get("total_losses") or 0),
                winrate=decimalize(metrics.get("winrate")),
                avg_rr=decimalize(metrics.get("avg_rr")),
                median_rr=decimalize(metrics.get("median_rr")),
                profit_factor=decimalize(metrics.get("profit_factor")),
                expectancy=decimalize(metrics.get("expectancy")),
                net_profit=decimalize(metrics.get("net_profit")),
                max_drawdown_abs=decimalize(metrics.get("max_drawdown_abs")),
                max_drawdown_pct=decimalize(metrics.get("max_drawdown_pct")),
                max_consecutive_losses=metrics.get("max_consecutive_losses"),
                avg_trade_duration_seconds=decimalize(metrics.get("avg_trade_duration_seconds")),
                metrics=json_safe(metrics),
            )
        )
        return metrics

    def refresh_all_metrics(self) -> list[dict[str, Any]]:
        run_ids = list(self.session.scalars(select(BacktestRun.id).order_by(BacktestRun.created_at)))
        return [{"run_id": run_id, "metrics": self.refresh_metrics(run_id)} for run_id in run_ids]

    def _calculate_metrics(self, run: BacktestRun) -> dict[str, Any]:
        events = list(
            self.session.scalars(select(SetupEvent.event_type).where(SetupEvent.run_id == run.id))
        )
        trade_rows = []
        for trade in self.session.scalars(select(Trade).where(Trade.run_id == run.id).order_by(Trade.entry_time)):
            trade_rows.append(
                {
                    "entry_time": trade.entry_time,
                    "exit_time": trade.exit_time,
                    "pnl": float(trade.pnl or 0),
                    "rr": float(trade.rr) if trade.rr is not None else None,
                }
            )
        trades = pd.DataFrame.from_records(trade_rows)
        metrics = summarize_trades(trades)
        event_series = pd.Series(events, dtype="object")
        metrics.update(
            {
                "total_h1_signals": int((event_series == "H1_SIGNAL").sum()) if not event_series.empty else 0,
                "total_setups": int((event_series == "M15_DOUBLE_SWING_VALIDATED").sum())
                if not event_series.empty
                else 0,
                "total_legs": int((event_series == "LEG_FOUND").sum()) if not event_series.empty else 0,
                "total_pd_selected": int(event_series.isin(["OB_SELECTED", "FVG_SELECTED"]).sum())
                if not event_series.empty
                else 0,
                "total_pd_touched": int((event_series == "PD_TOUCHED").sum()) if not event_series.empty else 0,
                "total_rejections": int((event_series == "REJECTION_CONFIRMED").sum()) if not event_series.empty else 0,
                "total_risk_rejected": int((event_series == "RISK_REJECTED").sum()) if not event_series.empty else 0,
            }
        )
        equity = self.session.execute(
            select(func.min(EquityCurve.drawdown_abs), func.min(EquityCurve.drawdown_pct)).where(
                EquityCurve.run_id == run.id
            )
        ).one()
        metrics["max_drawdown_abs"] = float(equity[0]) if equity[0] is not None else None
        metrics["max_drawdown_pct"] = float(equity[1]) if equity[1] is not None else None
        return metrics
