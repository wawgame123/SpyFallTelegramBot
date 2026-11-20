"""Add user_settings table

Revision ID: 0003
Revises: 0002
"""
from alembic import op

from app.database.models import UserSetting

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    UserSetting.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    UserSetting.__table__.drop(bind=op.get_bind(), checkfirst=True)
