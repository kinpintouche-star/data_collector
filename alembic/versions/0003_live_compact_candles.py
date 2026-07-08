"""live compact candles

Revision ID: 0003_live_compact_candles
Revises: 0002_live_collector_schema
Create Date: 2026-07-06
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "0003_live_compact_candles"
down_revision = "0002_live_collector_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    base_dir = Path(__file__).resolve().parents[2]
    _execute_sql_file(base_dir / "sql" / "003_live_compact_candles.sql")


def _execute_sql_file(path: Path) -> None:
    for statement in _split_sql(path.read_text(encoding="utf-8")):
        op.execute(statement)


def _split_sql(sql: str) -> list[str]:
    return [statement.strip() for statement in sql.split(";") if statement.strip()]


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS live_market_candles")
