"""Add reason column to user_penalties

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("user_penalties")}
    if "reason" not in existing:
        op.add_column(
            "user_penalties",
            sa.Column("reason", sa.String(16), nullable=False, server_default="leave"),
        )


def downgrade() -> None:
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("user_penalties")}
    if "reason" in existing:
        op.drop_column("user_penalties", "reason")
