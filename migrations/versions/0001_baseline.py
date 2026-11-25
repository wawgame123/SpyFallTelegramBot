"""Baseline: create missing tables (idempotent for databases made by create_all)

Revision ID: 0001
Revises:
"""
from alembic import op

from app.models.database.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    pass
