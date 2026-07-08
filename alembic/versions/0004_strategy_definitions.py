"""strategy definitions

Revision ID: 0004_strategy_definitions
Revises: 0003_live_compact_candles
Create Date: 2026-07-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0004_strategy_definitions"
down_revision = "0003_live_compact_candles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="draft", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("definition_hash", sa.Text(), nullable=False),
        sa.Column("exported_path", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("name", "version", name="uq_strategy_definitions_name_version"),
    )
    op.create_index("idx_strategy_definitions_status", "strategy_definitions", ["status"])
    op.create_index("idx_strategy_definitions_hash", "strategy_definitions", ["definition_hash"])


def downgrade() -> None:
    op.drop_index("idx_strategy_definitions_hash", table_name="strategy_definitions")
    op.drop_index("idx_strategy_definitions_status", table_name="strategy_definitions")
    op.drop_table("strategy_definitions")
