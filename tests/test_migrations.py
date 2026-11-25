from sqlalchemy import create_engine, inspect, text

from app.models.database.migrate import run_migrations
from app.models.database.session import make_session_factory


def test_fresh_database_gets_all_tables_and_version(tmp_path):
    url = f"sqlite:///{tmp_path}/fresh.db"
    run_migrations(url)
    inspector = inspect(create_engine(url))
    tables = set(inspector.get_table_names())
    assert {"rooms", "question_packs", "round_players", "user_settings", "web_friends",
            "avatar_appearances", "web_sessions", "alembic_version"} <= tables
    columns = {c["name"] for c in inspector.get_columns("question_packs")}
    assert {"is_hidden", "owner_name"} <= columns
    with create_engine(url).connect() as conn:
        assert conn.execute(text("select version_num from alembic_version")).scalar() == "0014"


def test_avatar_placement_migration_keeps_existing_accessories(tmp_path):
    url = f"sqlite:///{tmp_path}/appearance.db"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('0012')"))
        connection.execute(text(
            "CREATE TABLE avatar_appearances (user_id BIGINT PRIMARY KEY, hat VARCHAR(24), face VARCHAR(24))"
        ))
        connection.execute(text("INSERT INTO avatar_appearances VALUES (42, 'fedora', 'cat')"))
    run_migrations(url)
    with engine.connect() as connection:
        look = connection.execute(text("SELECT hat, face, placement, outfit FROM avatar_appearances")).one()
        assert tuple(look) == ("fedora", "cat", None, None)


def test_legacy_database_is_upgraded_without_data_loss(tmp_path):
    url = f"sqlite:///{tmp_path}/legacy.db"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE question_packs (id INTEGER PRIMARY KEY, slug VARCHAR(32), "
                "title VARCHAR(100), description VARCHAR(255), category_id INTEGER, "
                "owner_id BIGINT, is_public BOOLEAN, position INTEGER, created_at DATETIME, updated_at DATETIME)"
            )
        )
        conn.execute(
            text("INSERT INTO question_packs (id, slug, title, is_public) VALUES (1, 'animals', 'Животные', 1)")
        )
    run_migrations(url)
    with create_engine(url).connect() as conn:
        row = conn.execute(text("select title, is_hidden, owner_name from question_packs")).one()
    assert row[0] == "Животные" and not row[1] and row[2] is None


def test_migrations_are_idempotent_and_compatible_with_session_factory(tmp_path):
    url = f"sqlite:///{tmp_path}/twice.db"
    run_migrations(url)
    run_migrations(url)
    factory = make_session_factory(url)
    assert factory is not None


def test_legacy_game_sessions_get_is_test_column(tmp_path):
    url = f"sqlite:///{tmp_path}/legacy2.db"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE game_sessions (id INTEGER PRIMARY KEY, room_id INTEGER, room_code VARCHAR(8), "
                "title VARCHAR(100), host_id BIGINT, status VARCHAR(16), started_at DATETIME, ended_at DATETIME)"
            )
        )
        conn.execute(text("INSERT INTO game_sessions (id, room_code, title, host_id, status) VALUES (1, 'ABC', 'Старое', 1, 'finished')"))
    run_migrations(url)
    with create_engine(url).connect() as conn:
        row = conn.execute(text("select title, is_test from game_sessions")).one()
    assert row[0] == "Старое" and not row[1]
