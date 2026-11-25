"""Store avatar outfits and remembered browser sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "outfit" not in {column["name"] for column in inspector.get_columns("avatar_appearances")}:
        op.add_column("avatar_appearances", sa.Column("outfit", sa.String(24), nullable=True))
    if not inspector.has_table("web_sessions"):
        op.create_table(
            "web_sessions",
            sa.Column("digest", sa.String(64), primary_key=True),
            sa.Column("scope", sa.String(64), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("name", sa.String(80), nullable=False),
            sa.Column("expires_at", sa.BigInteger(), nullable=False),
        )
        op.create_index("ix_web_sessions_scope", "web_sessions", ["scope"])
        op.create_index("ix_web_sessions_expires_at", "web_sessions", ["expires_at"])


def downgrade():
    op.drop_table("web_sessions")
    op.drop_column("avatar_appearances", "outfit")
