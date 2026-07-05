"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-07-05
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[2] / "sql" / "001_initial_schema.sql"
    _execute_sql_file(sql_path)
    views_dir = sql_path.parent / "views"
    for view_path in sorted(views_dir.glob("mart_*.sql")):
        _execute_sql_file(view_path)


def _execute_sql_file(path: Path) -> None:
    for statement in _split_sql(path.read_text(encoding="utf-8")):
        op.execute(statement)


def _split_sql(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";") if statement.strip()]


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS mart_dataset_quality")
    op.execute("DROP VIEW IF EXISTS mart_source_performance")
    op.execute("DROP VIEW IF EXISTS mart_session_performance")
    op.execute("DROP VIEW IF EXISTS mart_pd_array_performance")
    op.execute("DROP VIEW IF EXISTS mart_parameter_performance")
    op.execute("DROP VIEW IF EXISTS mart_symbol_performance")
    op.execute("DROP VIEW IF EXISTS mart_monthly_performance")
    op.execute("DROP VIEW IF EXISTS mart_setup_funnel")
    op.execute("DROP VIEW IF EXISTS mart_run_summary")
    op.execute("DROP TABLE IF EXISTS run_metrics")
    op.execute("DROP TABLE IF EXISTS equity_curve")
    op.execute("DROP TABLE IF EXISTS trades")
    op.execute("DROP TABLE IF EXISTS fills")
    op.execute("DROP TABLE IF EXISTS orders")
    op.execute("DROP TABLE IF EXISTS setup_events")
    op.execute("DROP TABLE IF EXISTS backtest_runs")
    op.execute("DROP TABLE IF EXISTS parameter_sets")
    op.execute("DROP TABLE IF EXISTS strategy_versions")
    op.execute("DROP TABLE IF EXISTS datasets")
    op.execute("DROP TABLE IF EXISTS data_import_jobs")
    op.execute("DROP TABLE IF EXISTS market_candles")
    op.execute("DROP TABLE IF EXISTS symbol_aliases")
    op.execute("DROP TABLE IF EXISTS symbols")
    op.execute("DROP TABLE IF EXISTS data_sources")
