from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import sessionmaker

from app.database.models import UserSetting

OFFSETS = [-8, -5, -3, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
OFFSET_HINTS = {
    -8: "Лос-Анджелес",
    -5: "Нью-Йорк",
    -3: "Бразилия",
    0: "Лондон",
    1: "Берлин",
    2: "Калининград",
    3: "Москва",
    4: "Самара",
    5: "Екатеринбург",
    6: "Омск",
    7: "Новосибирск",
    8: "Иркутск",
    9: "Якутск",
    10: "Владивосток",
    11: "Магадан",
    12: "Камчатка",
}


def offset_label(hours: int) -> str:
    sign = "+" if hours >= 0 else "-"
    return f"UTC{sign}{abs(hours)}"


def zone_title(hours: int) -> str:
    hint = OFFSET_HINTS.get(hours)
    return f"{hint} ({offset_label(hours)})" if hint else offset_label(hours)


def local_time(now_utc: datetime, hours: int) -> str:
    return (now_utc + timedelta(hours=hours)).strftime("%H:%M")


@dataclass(frozen=True)
class Prefs:
    tz_minutes: int = 0
    emoji: bool = False
    colors: bool = False


class SettingsService:
    def __init__(self, factory: sessionmaker):
        self._factory = factory
        self._cache: dict[int, Prefs] = {}

    def prefs(self, user_id: int) -> Prefs:
        cached = self._cache.get(user_id)
        if cached is not None:
            return cached
        with self._factory() as db:
            row = db.get(UserSetting, user_id)
            prefs = (
                Prefs(row.tz_offset_min, bool(row.emoji), bool(row.colors)) if row else Prefs()
            )
        self._cache[user_id] = prefs
        return prefs

    def tz_minutes(self, user_id: int) -> int:
        return self.prefs(user_id).tz_minutes

    def emoji_enabled(self, user_id: int) -> bool:
        return self.prefs(user_id).emoji

    def colors_enabled(self, user_id: int) -> bool:
        return self.prefs(user_id).colors

    def _update(self, user_id: int, **changes) -> None:
        with self._factory.begin() as db:
            row = db.get(UserSetting, user_id)
            if row is None:
                row = UserSetting(user_id=user_id, tz_offset_min=0, emoji=False, colors=False)
                db.add(row)
            for key, value in changes.items():
                setattr(row, key, value)
        self._cache.pop(user_id, None)

    def set_tz_hours(self, user_id: int, hours: int) -> None:
        if hours not in OFFSETS:
            raise ValueError("Недопустимый часовой пояс")
        self._update(user_id, tz_offset_min=hours * 60)

    def set_emoji(self, user_id: int, enabled: bool) -> None:
        self._update(user_id, emoji=bool(enabled))

    def set_colors(self, user_id: int, enabled: bool) -> None:
        self._update(user_id, colors=bool(enabled))
