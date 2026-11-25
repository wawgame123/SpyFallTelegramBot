"""Store the player's equipped avatar accessories."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("avatar_appearances"):
        op.create_table(
            "avatar_appearances",
            sa.Column("user_id", sa.BigInteger(), primary_key=True, autoincrement=False),
            sa.Column("hat", sa.String(24)),
            sa.Column("face", sa.String(24)),
        )


def downgrade():
    op.drop_table("avatar_appearances")
