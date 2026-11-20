from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.database.models import Base

COLUMN_MIGRATIONS = {
    "rooms": [("last_activity_at", "DATETIME NULL")],
    "match_rounds": [("pack_id", "INTEGER NULL"), ("pack_title", "VARCHAR(100) NULL")],
    "game_sessions": [("is_test", "BOOLEAN NOT NULL DEFAULT 0")],
    "user_penalties": [
        ("reason", "VARCHAR(16) NOT NULL DEFAULT 'leave'"),
        ("early_guess_warnings", "JSON NULL"),
    ],
    "user_settings": [
        ("emoji", "BOOLEAN NOT NULL DEFAULT 0"),
        ("colors", "BOOLEAN NOT NULL DEFAULT 0"),
    ],
    "question_packs": [
        ("is_hidden", "BOOLEAN NOT NULL DEFAULT 0"),
        ("owner_name", "VARCHAR(40) NULL"),
    ],
}


def ensure_schema(engine) -> None:
    inspector = inspect(engine)
    for table, columns in COLUMN_MIGRATIONS.items():
        if not inspector.has_table(table):
            continue
        existing = {col["name"] for col in inspector.get_columns(table)}
        with engine.begin() as conn:
            for name, ddl in columns:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def make_session_factory(url: str) -> sessionmaker:
    connect_args = {}
    if url.startswith("mysql+pymysql:"):
        connect_args = {"connect_timeout": 10, "read_timeout": 30, "write_timeout": 30}
    engine = create_engine(
        url, future=True, pool_pre_ping=True, pool_recycle=1800,
        connect_args=connect_args,
    )
    Base.metadata.create_all(engine)
    ensure_schema(engine)
    return sessionmaker(engine, expire_on_commit=False)
