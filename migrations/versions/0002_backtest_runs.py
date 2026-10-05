"""Backtest runs table

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04

Fresh databases get this table from revision 0001 (it builds the current metadata); databases created
before the backtest feature get it here. The guard makes the revision safe in both cases.
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "backtest_runs" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("user_id", sa.BigInteger, nullable=False, index=True),
        sa.Column("strategy_id", sa.String(16), nullable=False),
        sa.Column("strategy_version_id", sa.String(16), nullable=False),
        sa.Column("account_id", sa.String(16), nullable=False),
        sa.Column("symbols", sa.JSON, nullable=False),
        sa.Column("timeframe", sa.String(4), nullable=False),
        sa.Column("date_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("date_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("config", sa.JSON, nullable=False),
        sa.Column("status", sa.String(12), nullable=False, index=True),
        sa.Column("bars_processed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("summary", sa.JSON, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("backtest_runs")
