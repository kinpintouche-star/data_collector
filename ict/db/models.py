from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func, text


class Base(DeclarativeBase):
    pass


class JsonMixin:
    @staticmethod
    def json_default() -> dict:
        return {}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DataSource(Base, TimestampMixin, JsonMixin):
    __tablename__ = "data_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    base_timezone: Mapped[str] = mapped_column(Text, default="UTC", server_default="UTC")
    priority: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)

    aliases: Mapped[list["SymbolAlias"]] = relationship(back_populates="source")


class Symbol(Base, TimestampMixin, JsonMixin):
    __tablename__ = "symbols"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    asset_type: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    base_currency: Mapped[Optional[str]] = mapped_column(Text)
    quote_currency: Mapped[Optional[str]] = mapped_column(Text)
    price_currency: Mapped[Optional[str]] = mapped_column(Text)
    exchange: Mapped[Optional[str]] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, default="UTC", server_default="UTC", nullable=False)
    tick_size: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    tick_value: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    point_size: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    contract_size: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )

    aliases: Mapped[list["SymbolAlias"]] = relationship(back_populates="symbol", cascade="all, delete-orphan")
    candles: Mapped[list["MarketCandle"]] = relationship(back_populates="symbol")


class SymbolAlias(Base, TimestampMixin, JsonMixin):
    __tablename__ = "symbol_aliases"
    __table_args__ = (
        UniqueConstraint("source_id", "source_symbol", name="uq_symbol_aliases_source_symbol"),
        UniqueConstraint("symbol_id", "source_id", "source_symbol", name="uq_symbol_aliases_symbol_source_symbol"),
        Index("idx_symbol_aliases_symbol_source", "symbol_id", "source_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("symbols.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False
    )
    source_symbol: Mapped[str] = mapped_column(Text, nullable=False)
    source_exchange: Mapped[Optional[str]] = mapped_column(Text)
    source_asset_type: Mapped[Optional[str]] = mapped_column(Text)
    source_timezone: Mapped[Optional[str]] = mapped_column(Text)
    min_timeframe: Mapped[Optional[str]] = mapped_column(Text)
    max_timeframe: Mapped[Optional[str]] = mapped_column(Text)
    price_multiplier: Mapped[Decimal] = mapped_column(Numeric, default=1, server_default="1", nullable=False)
    tick_size_override: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    point_size_override: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), nullable=False)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )

    symbol: Mapped[Symbol] = relationship(back_populates="aliases")
    source: Mapped[DataSource] = relationship(back_populates="aliases")


class MarketCandle(Base, JsonMixin):
    __tablename__ = "market_candles"
    __table_args__ = (
        UniqueConstraint(
            "symbol_id",
            "source_id",
            "timeframe",
            "time_open",
            name="uq_market_candles_symbol_source_tf_time",
        ),
        Index("idx_market_candles_symbol_source_tf_time", "symbol_id", "source_id", "timeframe", "time_open"),
        Index("idx_market_candles_time", "time_open"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("symbols.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False
    )
    timeframe: Mapped[str] = mapped_column(Text, nullable=False)
    time_open: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    tick_volume: Mapped[Optional[int]] = mapped_column(BigInteger)
    real_volume: Mapped[Optional[int]] = mapped_column(BigInteger)
    spread: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    source_symbol: Mapped[Optional[str]] = mapped_column(Text)
    quality_flags: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    symbol: Mapped[Symbol] = relationship(back_populates="candles")
    source: Mapped[DataSource] = relationship()


class DataImportJob(Base, JsonMixin):
    __tablename__ = "data_import_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("data_sources.id"), nullable=False)
    symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbols.id"), nullable=False)
    alias_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_aliases.id"))
    timeframe: Mapped[str] = mapped_column(Text, nullable=False)
    requested_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requested_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, default="created", server_default="created", nullable=False)
    rows_fetched: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rows_inserted: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rows_updated: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rows_skipped: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    source_params: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)
    quality_report: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Dataset(Base, JsonMixin):
    __tablename__ = "datasets"
    __table_args__ = (
        UniqueConstraint(
            "symbol_id",
            "source_id",
            "timeframe",
            "start_time",
            "end_time",
            "dataset_version",
            name="uq_datasets_symbol_source_tf_time_version",
        ),
        Index("idx_datasets_symbol_source_time", "symbol_id", "source_id", "timeframe", "start_time", "end_time"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbols.id"), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("data_sources.id"), nullable=False)
    timeframe: Mapped[str] = mapped_column(Text, nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dataset_name: Mapped[Optional[str]] = mapped_column(Text)
    dataset_version: Mapped[str] = mapped_column(Text, default="1", server_default="1", nullable=False)
    candles_count: Mapped[Optional[int]] = mapped_column(Integer)
    missing_candles_count: Mapped[Optional[int]] = mapped_column(Integer)
    duplicate_candles_count: Mapped[Optional[int]] = mapped_column(Integer)
    quality_score: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    checksum: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="created", server_default="created", nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StrategyVersion(Base, JsonMixin):
    __tablename__ = "strategy_versions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_strategy_versions_name_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="python", server_default="python")
    source_reference: Mapped[Optional[str]] = mapped_column(Text)
    git_commit: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ParameterSet(Base, JsonMixin):
    __tablename__ = "parameter_sets"
    __table_args__ = (Index("idx_parameter_sets_params_gin", "params", postgresql_using="gin"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategy_versions.id")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    params_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BacktestRun(Base, JsonMixin):
    __tablename__ = "backtest_runs"
    __table_args__ = (
        Index("idx_backtest_runs_dataset_created", "dataset_id", "created_at"),
        Index("idx_backtest_runs_symbol_created", "symbol_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("strategy_versions.id"), nullable=False
    )
    parameter_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parameter_sets.id"), nullable=False
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False)
    symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbols.id"), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("data_sources.id"), nullable=False)
    run_type: Mapped[str] = mapped_column(Text, nullable=False, default="backtest", server_default="backtest")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="created", server_default="created")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    initial_balance: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    final_balance: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class SetupEvent(Base, JsonMixin):
    __tablename__ = "setup_events"
    __table_args__ = (
        Index("idx_setup_events_run_time", "run_id", "event_time"),
        Index("idx_setup_events_type", "event_type"),
        Index("idx_setup_events_setup", "run_id", "setup_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False)
    symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbols.id"), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("data_sources.id"), nullable=False)
    setup_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    direction: Mapped[Optional[str]] = mapped_column(Text)
    price: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    state_before: Mapped[Optional[str]] = mapped_column(Text)
    state_after: Mapped[Optional[str]] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Order(Base, JsonMixin):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False)
    setup_id: Mapped[Optional[str]] = mapped_column(Text)
    symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbols.id"), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("data_sources.id"), nullable=False)
    order_type: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    requested_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requested_price: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    volume: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    sl: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    tp: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    external_order_id: Mapped[Optional[str]] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Fill(Base, JsonMixin):
    __tablename__ = "fills"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False)
    order_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id"))
    symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbols.id"), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("data_sources.id"), nullable=False)
    fill_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fill_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    commission: Mapped[Decimal] = mapped_column(Numeric, default=0, server_default="0")
    slippage: Mapped[Decimal] = mapped_column(Numeric, default=0, server_default="0")
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Trade(Base, JsonMixin):
    __tablename__ = "trades"
    __table_args__ = (
        Index("idx_trades_run_entry", "run_id", "entry_time"),
        Index("idx_trades_symbol_entry", "symbol_id", "entry_time"),
        Index("idx_trades_dataset_entry", "dataset_id", "entry_time"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False)
    setup_id: Mapped[str] = mapped_column(Text, nullable=False)
    symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbols.id"), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("data_sources.id"), nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    exit_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    exit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    volume: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    sl: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    tp: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    exit_reason: Mapped[Optional[str]] = mapped_column(Text)
    pnl: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    pnl_points: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    rr: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    mae: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    mfe: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    pd_type: Mapped[Optional[str]] = mapped_column(Text)
    strategy_mode: Mapped[Optional[str]] = mapped_column(Text)
    session_name: Mapped[Optional[str]] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EquityCurve(Base, JsonMixin):
    __tablename__ = "equity_curve"
    __table_args__ = (
        UniqueConstraint("run_id", "time", name="uq_equity_curve_run_time"),
        Index("idx_equity_curve_run_time", "run_id", "time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    equity: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    drawdown_abs: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    drawdown_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    open_positions: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RunMetric(Base, JsonMixin):
    __tablename__ = "run_metrics"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backtest_runs.id", ondelete="CASCADE"), primary_key=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("datasets.id"), nullable=False)
    total_h1_signals: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_setups: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_legs: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_pd_selected: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_pd_touched: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_rejections: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_risk_rejected: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_trades: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_wins: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_losses: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    winrate: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    avg_rr: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    median_rr: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    profit_factor: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    expectancy: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    net_profit: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    max_drawdown_abs: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    max_drawdown_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    max_consecutive_losses: Mapped[Optional[int]] = mapped_column(Integer)
    avg_trade_duration_seconds: Mapped[Optional[Decimal]] = mapped_column(Numeric)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
