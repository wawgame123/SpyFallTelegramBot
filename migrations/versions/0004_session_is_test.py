"""Mark test lobbies in game_sessions

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("game_sessions")}


def upgrade() -> None:
    if "is_test" not in _columns():
        op.add_column(
            "game_sessions",
            sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        )


def downgrade() -> None:
    if "is_test" in _columns():
        op.drop_column("game_sessions", "is_test")
