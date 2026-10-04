"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2026-01-01

The baseline is generated from the declarative metadata so it always matches app/models at this
revision. Subsequent schema changes MUST be new revisions (`alembic revision --autogenerate -m ...`).
"""
from alembic import op

import app.models  # noqa: F401
from app.database.base import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
