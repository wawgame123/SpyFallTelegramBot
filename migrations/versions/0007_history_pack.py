"""Preserve the exact pack used in each round, independently of deletion."""
import json
import sqlalchemy as sa
from alembic import op
revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("match_rounds")}
    for name, kind in [("pack_id", sa.Integer()), ("pack_title", sa.String(100))]:
        if name not in columns:
            op.add_column("match_rounds", sa.Column(name, kind, nullable=True))

    # Recover only the exact current round still stored in a room snapshot.
    # Never infer an old pack from its potentially non-unique or renamed title.
    conn = op.get_bind()
    for raw in conn.execute(sa.text("SELECT state FROM rooms")).scalars():
        try:
            state = json.loads(raw)
            current = state.get("round") or {}
            slug = current.get("theme_id")
            sid = state.get("session_id")
            number = state.get("round_no")
            word = current.get("word")
            if not (slug and sid and number and word):
                continue
            pid = conn.execute(sa.text("SELECT id FROM question_packs WHERE slug=:slug"), {"slug": slug}).scalar_one_or_none()
            if pid is not None:
                conn.execute(sa.text("UPDATE match_rounds SET pack_id=:pid, pack_title=theme WHERE pack_id IS NULL AND session_id=:sid AND round_no=:number AND word=:word"), {"pid": pid, "sid": sid, "number": number, "word": word})
        except (ValueError, TypeError, AttributeError):
            continue

def downgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("match_rounds")}
    for name in ("pack_title", "pack_id"):
        if name in columns:
            op.drop_column("match_rounds", name)
