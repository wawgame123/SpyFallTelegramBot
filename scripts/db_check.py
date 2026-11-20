import os
import sys

from dotenv import load_dotenv

from app.database.info import check_connection, describe, ensure_mysql, format_info, mask_url
from app.database.session import make_session_factory

DEFAULT_URL = "mysql+pymysql://spy:spy@localhost:3306/spy_game?charset=utf8mb4"


def run(url: str, require_mysql: bool = True) -> str:
    if require_mysql:
        ensure_mysql(url)
    check_connection(url)
    factory = make_session_factory(url)
    return f"Подключение: {mask_url(url)}\n\n" + format_info(describe(factory, url))


def main() -> int:
    load_dotenv()
    url = os.getenv("DATABASE_URL", DEFAULT_URL)
    try:
        print(run(url))
    except Exception as err:
        print(f"Не удалось проверить базу данных: {err}")
        print(f"Подключение: {mask_url(url)}")
        print("Проверьте, что служба MySQL запущена, а логин и пароль в DATABASE_URL верны.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
