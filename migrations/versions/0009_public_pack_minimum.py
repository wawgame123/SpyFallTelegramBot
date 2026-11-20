"""Unpublish existing user packs that violate the ten-word publication minimum."""
import sqlalchemy as sa
from alembic import op
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    packs = sa.table("question_packs", sa.column("id", sa.Integer()), sa.column("owner_id", sa.BigInteger()), sa.column("is_public", sa.Boolean()))
    words = sa.table("pack_words", sa.column("id", sa.Integer()), sa.column("pack_id", sa.Integer()))
    ids = conn.execute(sa.select(packs.c.id).select_from(packs.outerjoin(words, words.c.pack_id == packs.c.id)).where(packs.c.is_public.is_(True), packs.c.owner_id.is_not(None)).group_by(packs.c.id).having(sa.func.count(words.c.id) < 10)).scalars().all()
    for start in range(0, len(ids), 500):
        conn.execute(packs.update().where(packs.c.id.in_(ids[start:start+500])).values(is_public=False))


def downgrade():
    # Re-publishing invalid or intentionally private packs would be unsafe.
    pass
