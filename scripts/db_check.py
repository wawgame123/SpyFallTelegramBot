import os
import sys

from dotenv import load_dotenv

from app.database.info import check_connection, describe, ensure_supported_database, format_info, mask_url
from app.database.session import make_session_factory

DEFAULT_URL = "mysql+pymysql://spy:spy@localhost:3306/spy_game?charset=utf8mb4"


def run(url: str) -> str:
    ensure_supported_database(url)
    check_connection(url)
    factory = make_session_factory(url)
    try:
        return f"Подключение: {mask_url(url)}\n\n" + format_info(describe(factory, url))
    finally:
        factory.kw["bind"].dispose()


def main() -> int:
    load_dotenv()
    url = os.getenv("DATABASE_URL", DEFAULT_URL)
    try:
        print(run(url))
    except Exception as err:
        print(f"Не удалось проверить базу данных: {err}")
        print(f"Подключение: {mask_url(url)}")
        print("Проверьте адрес MySQL и учётные данные или доступ к файлу SQLite.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
