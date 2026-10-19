"""Baseline schema for all monitoring tables.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-19

The baseline creates the schema straight from the ORM metadata; incremental
revisions generated later with `alembic revision --autogenerate` then diff
against that same metadata.
"""

from __future__ import annotations

from alembic import op

from signals.db.models import Base

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
