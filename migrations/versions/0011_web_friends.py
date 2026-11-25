"""Add personal friend lists for the website."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("web_friends"):
        op.create_table(
            "web_friends",
            sa.Column("user_id", sa.BigInteger(), primary_key=True, autoincrement=False),
            sa.Column("friend_id", sa.BigInteger(), primary_key=True, autoincrement=False),
            sa.Column("name", sa.String(80), nullable=False),
        )


def downgrade():
    op.drop_table("web_friends")
