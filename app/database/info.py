from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.database.models import Base


def ensure_mysql(url: str) -> None:
    backend = make_url(url).get_backend_name()
    if backend != "mysql":
        raise RuntimeError(
            f"Бот работает только с MySQL, а в DATABASE_URL указан «{backend}». "
            "Пример: mysql+pymysql://spy:spy@localhost:3306/spy_game?charset=utf8mb4"
        )


def mask_url(url: str) -> str:
    return make_url(url).render_as_string(hide_password=True)


def check_connection(url: str) -> None:
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    finally:
        engine.dispose()


def describe(factory: sessionmaker, url: str) -> dict:
    parsed = make_url(url)
    with factory() as db:
        bind = db.connection().engine
        dialect = bind.dialect.name
        version_info = bind.dialect.server_version_info or ()
        version = ".".join(str(part) for part in version_info) or "?"
        existing = set(inspect(db.connection()).get_table_names())
        counts = {}
        for table in Base.metadata.sorted_tables:
            if table.name in existing:
                counts[table.name] = db.execute(select(func.count()).select_from(table)).scalar_one()
    return {
        "dialect": dialect,
        "version": version,
        "host": parsed.host or "-",
        "port": parsed.port or "-",
        "database": parsed.database or "-",
        "tables": counts,
    }


def format_info(info: dict) -> str:
    lines = [
        f"База данных: {info['dialect']} {info['version']}",
        f"Сервер: {info['host']}:{info['port']}, схема {info['database']}",
        "",
        "Строк в таблицах:",
    ]
    lines += [f"{name}: {count}" for name, count in info["tables"].items()]
    return "\n".join(lines)
