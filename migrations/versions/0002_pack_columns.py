"""Add moderation and author columns to question_packs

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("question_packs")}


def upgrade() -> None:
    existing = _columns()
    if "is_hidden" not in existing:
        op.add_column(
            "question_packs",
            sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )
    if "owner_name" not in existing:
        op.add_column("question_packs", sa.Column("owner_name", sa.String(40), nullable=True))


def downgrade() -> None:
    existing = _columns()
    if "owner_name" in existing:
        op.drop_column("question_packs", "owner_name")
    if "is_hidden" in existing:
        op.drop_column("question_packs", "is_hidden")
