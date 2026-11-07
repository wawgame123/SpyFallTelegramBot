import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str
    admin_username: str = "WAWGAME"
    admin_id: int | None = None
    proxy_url: str | None = None


def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Переменная окружения BOT_TOKEN не задана")
    raw_admin_id = os.getenv("ADMIN_ID", "").strip()
    return Settings(
        bot_token=token,
        admin_username=os.getenv("ADMIN_USERNAME", "WAWGAME").strip() or "WAWGAME",
        admin_id=int(raw_admin_id) if raw_admin_id.isdigit() else None,
        proxy_url=os.getenv("PROXY_URL", "").strip() or None,
        database_url=os.getenv("DATABASE_URL", "mysql+pymysql://spy:spy@localhost:3306/spy_game?charset=utf8mb4"),
    )
