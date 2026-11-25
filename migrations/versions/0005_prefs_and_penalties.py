"""Add emoji/colors preferences and leave penalties

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

from app.models.database.models import UserPenalty

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("user_settings")}
    for name in ("emoji", "colors"):
        if name not in existing:
            op.add_column(
                "user_settings",
                sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.text("0")),
            )
    UserPenalty.__table__.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    UserPenalty.__table__.drop(bind=bind, checkfirst=True)
    existing = {c["name"] for c in sa.inspect(bind).get_columns("user_settings")}
    for name in ("colors", "emoji"):
        if name in existing:
            op.drop_column("user_settings", name)
