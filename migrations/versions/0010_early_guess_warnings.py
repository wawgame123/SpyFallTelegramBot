"""Store timestamps for early online guessing warnings."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    columns = sa.inspect(op.get_bind()).get_columns("user_penalties")
    if not any(column["name"] == "early_guess_warnings" for column in columns):
        op.add_column("user_penalties", sa.Column("early_guess_warnings", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("user_penalties", "early_guess_warnings")
