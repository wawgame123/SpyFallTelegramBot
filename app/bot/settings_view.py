from datetime import datetime, timezone

from app.game.session import Screen
from app.services.settings_service import (
    OFFSET_HINTS,
    OFFSETS,
    Prefs,
    local_time,
    zone_title,
)


def _on_off(value: bool) -> str:
    return "включены" if value else "выключены"


def _coerce_prefs(prefs: Prefs | int) -> Prefs:
    return prefs if isinstance(prefs, Prefs) else Prefs(tz_minutes=int(prefs))


def settings_screen(prefs: Prefs | int, now_utc: datetime | None = None) -> Screen:
    prefs = _coerce_prefs(prefs)
    now_utc = now_utc or datetime.now(timezone.utc)
    current = prefs.tz_minutes // 60
    return Screen(
        "⚙️ Настройки\n\n"
        f"😀 Эмодзи на кнопках: {_on_off(prefs.emoji)}\n"
        f"Цветные кнопки: {_on_off(prefs.colors)}\n"
        f"🌐 Часовой пояс: {zone_title(current)}\n"
        f"🕒 Местное время: {local_time(now_utc, current)}",
        [
            [(f"Эмодзи: {'ВКЛ' if prefs.emoji else 'ВЫКЛ'}", f"set:emoji:{0 if prefs.emoji else 1}")],
            [(f"Цветные кнопки: {'ВКЛ' if prefs.colors else 'ВЫКЛ'}", f"set:colors:{0 if prefs.colors else 1}")],
            [("Часовой пояс", "set:tzmenu")],
            [("Закрыть", "menu:home")],
        ],
    )


def timezone_screen(prefs: Prefs | int, now_utc: datetime | None = None) -> Screen:
    prefs = _coerce_prefs(prefs)
    now_utc = now_utc or datetime.now(timezone.utc)
    current = prefs.tz_minutes // 60
    rows = []
    for hours in OFFSETS:
        city = OFFSET_HINTS.get(hours, "")
        sign = "+" if hours >= 0 else "-"
        label = f"{local_time(now_utc, hours)} · {city} · UTC{sign}{abs(hours)}"
        if hours == current:
            label = f"✓ {label}"
        rows.append([(label, f"set:tzset:{hours}")])
    rows.append([("← Назад к настройкам", "set:menu")])
    rows.append([("Закрыть", "menu:home")])
    return Screen(
        "🌐 Часовой пояс\n\n"
        "Выберите часовой пояс по местному времени. Изменение сохранится автоматически.\n\n"
        f"Сейчас выбран: {zone_title(current)} · {local_time(now_utc, current)}",
        rows,
    )
