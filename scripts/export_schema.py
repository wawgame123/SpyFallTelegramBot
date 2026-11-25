from pathlib import Path

from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.models.database.models import Base

OUTPUT = Path(__file__).resolve().parent / "schema.sql"


def build_schema() -> str:
    dialect = mysql.dialect()
    parts = [
        "-- Схема базы данных бота «Шпион» для MySQL 8.",
        "-- Файл только для справки: бот сам создаёт и обновляет таблицы при запуске (Alembic).",
        "-- Создан скриптом scripts/export_schema.py из app/models/database/models.py.",
        "",
    ]
    for table in Base.metadata.sorted_tables:
        parts.append(str(CreateTable(table).compile(dialect=dialect)).strip() + " DEFAULT CHARSET=utf8mb4;")
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            parts.append(str(CreateIndex(index).compile(dialect=dialect)).strip() + ";")
        parts.append("")
    return "\n".join(line.rstrip() for section in parts for line in section.split("\n"))


if __name__ == "__main__":
    OUTPUT.write_text(build_schema(), encoding="utf-8")
    print(f"Схема записана в {OUTPUT}")
