from datetime import timedelta, timezone
from html import escape

from app.game.session import Screen
from app.services.history_service import LobbyRound, LobbyRow, UserRound

PAGE_SIZE = 5

STATUS_TEXT = {
    "active": "идёт игра",
    "finished": "завершена",
    "abandoned": "прервана",
}
RESULT_TEXT = {"win": "победа", "loss": "поражение", "draw": "ничья"}
RESULT_ICON = {"win": "🏆", "loss": "●", "draw": "🤝"}
ROLE_TEXT = {"spy": "шпион", "civilian": "обычный игрок"}


def _shift(value, tz_minutes: int):
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value + timedelta(minutes=tz_minutes)


def tz_label(tz_minutes: int) -> str:
    sign = "+" if tz_minutes > 0 else "-"
    hours, minutes = divmod(abs(tz_minutes), 60)
    if minutes:
        return f"UTC{sign}{hours}:{minutes:02d}"
    return f"UTC{sign}{hours}" if hours else "UTC"


def fmt_dt(value, tz_minutes: int = 0) -> str:
    if not value:
        return "-"
    return _shift(value, tz_minutes).strftime("%d.%m.%Y %H:%M") + f" ({tz_label(tz_minutes)})"


def fmt_short(value, tz_minutes: int = 0) -> str:
    return _shift(value, tz_minutes).strftime("%d.%m %H:%M") if value else "-"


def outcome_text(outcome: str, spy_name: str) -> str:
    if outcome == "civilians_win":
        return "победили обычные игроки"
    if outcome == "spy_win_vote":
        return f"победил шпион {spy_name}: игроки не нашли шпиона"
    if outcome == "spy_win_guess":
        return f"победил шпион {spy_name}: он угадал слово"
    if outcome == "spy_left":
        return f"победили обычные игроки: шпион {spy_name} покинул игру"
    if outcome == "spy_wrong_guess":
        return f"победили обычные игроки: шпион {spy_name} ошибся при угадывании слова"
    return f"ничья: шпион {spy_name} угадал слово во время голосования"


def winners_text(row: LobbyRow) -> str:
    if row.winners:
        label = ", ".join(row.winners)
        return label if row.status != "active" else f"лидирует {label}"
    return "без победителя" if row.status != "active" else "пока нет лидера"


def _page_count(total: int) -> int:
    return max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)


def _pager(prefix: str, page: int, total: int, suffix: str = "") -> list[list[tuple[str, str]]]:
    pages = _page_count(total)
    nav = []
    if page > 0:
        nav.append(("← Предыдущая", f"{prefix}:{page - 1}{suffix}"))
    if page < pages - 1:
        nav.append(("Следующая →", f"{prefix}:{page + 1}{suffix}"))
    return [nav] if nav else []


def menu_screen() -> Screen:
    return Screen(
        "📚 История игр\n\n"
        "<b>Мои игры</b> · ваши роли, результаты и очки по раундам.\n"
        "<b>История лобби</b> · состав комнат и итоговый счёт.",
        [
            [("Мои игры", "hist:games:0"), ("История лобби", "hist:lobbies:0")],
            [("Закрыть", "menu:home")],
        ],
        html=True,
    )


def games_screen(rows: list[UserRound], total: int, page: int, tz_minutes: int = 0) -> Screen:
    back = [("К истории игр", "hist:menu")]
    if not rows:
        message = "На этой странице нет раундов." if total else "Вы ещё не сыграли ни одного онлайн-раунда."
        return Screen(f"<b>Мои игры</b>\n\n{message}", [[("К началу списка", "hist:games:0")], back] if total else [back], html=True)
    blocks, buttons = [], []
    for row in rows:
        pack = row.pack_title or row.theme
        result = RESULT_TEXT[row.result].capitalize()
        blocks.append(
            f"{RESULT_ICON[row.result]} <b>{result} · {row.score_delta:+d} оч.</b> · {fmt_short(row.finished_at, tz_minutes)}\n"
            f"{escape(row.lobby_title)} · раунд {row.round_no}\n"
            f"Набор: {escape(pack)} · слово: <b>{escape(row.word)}</b>\n"
            f"Вы — {ROLE_TEXT[row.role]} · шпион: {escape(row.spy_name)}\n"
            f"Итог: {escape(outcome_text(row.outcome, row.spy_name))}"
        )
        if row.round_id:
            context = f":g:{page}" if page else ""
            label = f"{fmt_short(row.finished_at, tz_minutes)} · {pack}"[:58]
            buttons.append([(label, f"hist:rd:{row.round_id}{context}")])
    text = f"<b>Мои игры</b> · {page + 1}/{_page_count(total)} · {tz_label(tz_minutes)}"
    return Screen(text + "\n\n" + "\n\n".join(blocks), buttons + _pager("hist:games", page, total) + [back], html=True)


def lobbies_screen(rows: list[LobbyRow], total: int, page: int, tz_minutes: int = 0) -> Screen:
    back = [("К истории игр", "hist:menu")]
    if not rows:
        message = "На этой странице нет лобби." if total else "Вы ещё не играли в онлайн-комнатах."
        return Screen(f"<b>История лобби</b>\n\n{message}", [[("К началу списка", "hist:lobbies:0")], back] if total else [back], html=True)
    blocks, buttons = [], []
    for row in rows:
        date = fmt_short(row.started_at, tz_minutes)
        blocks.append(
            f"<b>{escape(row.title)}</b> · {date}\n"
            f"{STATUS_TEXT.get(row.status, 'неизвестный статус').capitalize()} · "
            f"игроков {len(row.players)} · раундов {row.rounds_count}\n"
            f"🏆 {escape(winners_text(row))}"
        )
        buttons.append([(f"{date} · {row.title}"[:58], f"hist:lobby:{row.id}:{page}")])
    text = f"<b>История лобби</b> · {page + 1}/{_page_count(total)} · {tz_label(tz_minutes)}"
    return Screen(text + "\n\n" + "\n\n".join(blocks), buttons + _pager("hist:lobbies", page, total) + [back], html=True)


def lobby_screen(row: LobbyRow, tz_minutes: int = 0, list_page: int = 0) -> Screen:
    table = "\n".join(
        f"{i}. {escape(name)} · <b>{score}</b> оч." for i, (name, score) in enumerate(row.players, 1)
    )
    text = (
        f"<b>{escape(row.title)}</b>\n"
        f"{STATUS_TEXT.get(row.status, 'неизвестный статус').capitalize()} · {fmt_dt(row.started_at, tz_minutes)}\n\n"
        f"Победитель лобби: <b>{escape(winners_text(row))}</b>\n"
        f"Сыграно раундов: {row.rounds_count}\n\n"
        f"<b>Игроки и очки</b>\n{table}"
    )
    return Screen(
        text,
        [
            [("Раунды лобби", f"hist:lr:{row.id}:0:{list_page}")],
            *[[(name[:50], f"hist:sp:{row.id}:{uid}")] for uid, name in row.player_ids],
            [("К списку лобби", f"hist:lobbies:{list_page}")],
        ],
        html=True,
    )


def lobby_rounds_screen(
    session_id: int, rows: list[LobbyRound], total: int, page: int, tz_minutes: int = 0,
    list_page: int = 0,
) -> Screen:
    back = [("К лобби", f"hist:lobby:{session_id}:{list_page}")]
    if not rows:
        return Screen("На этой странице нет завершённых раундов.", [back])
    blocks, buttons = [], []
    for row in rows:
        pack = row.pack_title or row.theme
        points = ", ".join(f"{escape(name)} {delta:+d}" for name, _, delta in row.scores)
        blocks.append(
            f"<b>Раунд {row.round_no}</b> · {fmt_short(row.finished_at, tz_minutes)}\n"
            f"Набор: {escape(pack)} · слово: <b>{escape(row.word)}</b>\n"
            f"Шпион: {escape(row.spy_name)} · {escape(outcome_text(row.outcome, row.spy_name))}\n"
            f"Очки: {points}"
        )
        if row.id:
            buttons.append([(f"Раунд {row.round_no} · {pack}"[:58], f"hist:rd:{row.id}:l:{page}:{list_page}")])
    text = f"<b>Раунды лобби</b> · {page + 1}/{_page_count(total)} · {tz_label(tz_minutes)}"
    nav = _pager(f"hist:lr:{session_id}", page, total, f":{list_page}")
    return Screen(text + "\n\n" + "\n\n".join(blocks), buttons + nav + [back], html=True)
