from app.core.config import Settings


def is_admin(settings: Settings, user_id: int, username: str | None) -> bool:
    # Telegram usernames can change ownership; privileges belong to a stable ID.
    return settings.admin_id is not None and settings.admin_id > 0 and user_id == settings.admin_id
