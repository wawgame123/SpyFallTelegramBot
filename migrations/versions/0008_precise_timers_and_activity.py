"""Use double-precision epochs and separate human activity from technical writes."""
import json
import math
from datetime import datetime, timezone
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    for table, names in [("rooms", ("deadline",)), ("user_penalties", ("banned_until", "last_leave_at"))]:
        columns = {c["name"]: c for c in sa.inspect(conn).get_columns(table)}
        changes = [name for name in names if columns[name]["type"].__class__.__name__.upper() not in ("DOUBLE", "DOUBLE_PRECISION")]
        if changes:
            with op.batch_alter_table(table) as batch:
                for name in changes:
                    column = columns[name]
                    batch.alter_column(name, type_=sa.Double(), existing_type=column["type"], existing_nullable=column["nullable"], existing_server_default=column.get("default"))
    columns = {c["name"]: c for c in sa.inspect(conn).get_columns("rooms")}
    if "last_activity_at" not in columns:
        op.add_column("rooms", sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True))
    conn.execute(sa.text("UPDATE rooms SET last_activity_at=COALESCE(updated_at,created_at,:now) WHERE last_activity_at IS NULL").bindparams(sa.bindparam("now", type_=sa.DateTime())), {"now": datetime.now(timezone.utc).replace(tzinfo=None)})
    columns = {c["name"]: c for c in sa.inspect(conn).get_columns("rooms")}
    if columns["last_activity_at"]["nullable"]:
        with op.batch_alter_table("rooms") as batch:
            batch.alter_column("last_activity_at", existing_type=columns["last_activity_at"]["type"], nullable=False)
    if "ix_rooms_last_activity_at" not in {i["name"] for i in sa.inspect(conn).get_indexes("rooms")}:
        op.create_index("ix_rooms_last_activity_at", "rooms", ["last_activity_at"])
    # FLOAT precision already lost in old ban timestamps cannot be recreated.
    # Active room timers do have a canonical double-precision JSON snapshot.
    for row in conn.execute(sa.text("SELECT id,state FROM rooms WHERE deadline IS NOT NULL")).mappings().all():
        try:
            deadline = json.loads(row["state"]).get("deadline")
            if isinstance(deadline, (int, float)) and not isinstance(deadline, bool) and math.isfinite(deadline):
                conn.execute(sa.text("UPDATE rooms SET deadline=:deadline WHERE id=:id"), {"deadline": deadline, "id": row["id"]})
        except (ValueError, TypeError, AttributeError):
            continue


def downgrade():
    op.drop_index("ix_rooms_last_activity_at", table_name="rooms")
    with op.batch_alter_table("rooms") as batch:
        batch.drop_column("last_activity_at")
        batch.alter_column("deadline", type_=sa.Float(), existing_type=sa.Double(), existing_nullable=True)
    with op.batch_alter_table("user_penalties") as batch:
        for name in ("banned_until", "last_leave_at"):
            batch.alter_column(name, type_=sa.Float(), existing_type=sa.Double(), existing_nullable=False)
