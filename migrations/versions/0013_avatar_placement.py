"""Store placement, rotation, scale, and mirroring for avatar accessories."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    columns = sa.inspect(op.get_bind()).get_columns("avatar_appearances")
    if not any(column["name"] == "placement" for column in columns):
        op.add_column("avatar_appearances", sa.Column("placement", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("avatar_appearances", "placement")
