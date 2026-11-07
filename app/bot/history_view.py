from datetime import timedelta, timezone

from app.game.session import Screen
from app.services.history_service import LobbyRound, LobbyRow, UserRound

PAGE_SIZE = 5

STATUS_TEXT = {
    "active": "идёт игра",
    "finished": "завершена",
    "abandoned": "прервана",
}
RESULT_TEXT = {"win": "победа", "loss": "поражение", "draw": "ничья"}
ROLE_TEXT = {"spy": "шпион", "civilian": "обычный игрок"}


def _shift(value, tz_minutes: int):
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value + timedelta(minutes=tz_minutes)


def tz_label(tz_minutes: int) -> str:
    hours = tz_minutes // 60
    if hours == 0:
        return "UTC"
    return f"UTC{'+' if hours > 0 else '-'}{abs(hours)}"


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


def _pager(prefix: str, page: int, total: int) -> list[tuple[str, str]]:
    pages = max(1, -(-total // PAGE_SIZE))
    nav = []
    if page > 0:
        nav.append(("Назад", f"{prefix}:{page - 1}"))
    if page < pages - 1:
        nav.append(("Вперёд", f"{prefix}:{page + 1}"))
    return nav


def menu_screen() -> Screen:
    return Screen(
        "📚 История игр\n\nВыберите раздел, чтобы посмотреть свои результаты и завершённые комнаты.",
        [
            [("Мои игры", "hist:games:0")],
            [("История лобби", "hist:lobbies:0")],
            [("Закрыть", "menu:home")],
        ],
    )


def games_screen(rows: list[UserRound], total: int, page: int, tz_minutes: int = 0) -> Screen:
    if not rows:
        return Screen(
            "Мои игры\n\nВы ещё не сыграли ни одного онлайн-раунда.",
            [[("Назад", "hist:menu")]],
        )
    pages = max(1, -(-total // PAGE_SIZE))
    blocks = []
    for row in rows:
        sign = "+" if row.score_delta >= 0 else ""
        blocks.append(
            f"🕒 {fmt_dt(row.finished_at, tz_minutes)}\n"
            f"🎮 {row.lobby_title}\n"
            f"🎯 {row.word} · тема: {row.theme}\n"
            f"Набор: {row.pack_title or row.theme}\n"
            f"🕵️ {row.spy_name} · {outcome_text(row.outcome, row.spy_name)}\n"
            f"Ваша роль: {ROLE_TEXT[row.role]} · {RESULT_TEXT[row.result]}\n"
            f"⭐ Очки: {sign}{row.score_delta}"
        )
    nav = _pager("hist:games", page, total)
    buttons = [[(f"Раунд: {r.pack_title or r.theme}"[:55], f"hist:rd:{r.round_id}")] for r in rows if r.round_id]
    buttons += ([nav] if nav else []) + [[("Назад", "hist:menu")]]
    return Screen(
        f"Мои игры (страница {page + 1} из {pages})\n\n" + "\n\n".join(blocks), buttons
    )


def lobbies_screen(rows: list[LobbyRow], total: int, page: int, tz_minutes: int = 0) -> Screen:
    if not rows:
        return Screen(
            "История лобби\n\nВы ещё не играли в онлайн-комнатах.",
            [[("Назад", "hist:menu")]],
        )
    pages = max(1, -(-total // PAGE_SIZE))
    buttons = []
    for row in rows:
        label = f"{fmt_short(row.started_at, tz_minutes)}, {winners_text(row)}"
        buttons.append([(label[:60], f"hist:lobby:{row.id}")])
    nav = _pager("hist:lobbies", page, total)
    if nav:
        buttons.append(nav)
    buttons.append([("Назад", "hist:menu")])
    return Screen(
        f"История лобби (страница {page + 1} из {pages})\n\nНажмите на лобби, чтобы открыть подробности.",
        buttons,
    )


def lobby_screen(row: LobbyRow, tz_minutes: int = 0) -> Screen:
    table = "\n".join(
        f"{i}. {name}: {score}" for i, (name, score) in enumerate(row.players, 1)
    )
    text = (
        f"{row.title}\n\n"
        f"Начало: {fmt_dt(row.started_at, tz_minutes)}\n"
        f"Статус: {STATUS_TEXT.get(row.status, row.status)}\n"
        f"Сыграно раундов: {row.rounds_count}\n"
        f"Победитель лобби: {winners_text(row)}\n\n"
        f"Игроки и очки:\n{table}"
    )
    return Screen(
        text,
        [
            *[[(name[:50], f"hist:sp:{row.id}:{uid}")] for uid, name in row.player_ids],
            [("Игры этого лобби", f"hist:lr:{row.id}:0")],
            [("К списку лобби", "hist:lobbies:0")],
        ],
    )


def lobby_rounds_screen(
    session_id: int, rows: list[LobbyRound], total: int, page: int, tz_minutes: int = 0
) -> Screen:
    back = [("К лобби", f"hist:lobby:{session_id}")]
    if not rows:
        return Screen("В этом лобби пока нет завершённых раундов.", [back])
    pages = max(1, -(-total // PAGE_SIZE))
    blocks = []
    for row in rows:
        points = ", ".join(
            f"{name} {'+' if delta >= 0 else ''}{delta}" for name, _, delta in row.scores
        )
        blocks.append(
            f"Раунд {row.round_no}, {fmt_dt(row.finished_at, tz_minutes)}\n"
            f"Тема: {row.theme}. Слово: {row.word}\n"
            f"Набор: {row.pack_title or row.theme}\n"
            f"Шпион: {row.spy_name}, {outcome_text(row.outcome, row.spy_name)}\n"
            f"Очки: {points}"
        )
    nav = _pager(f"hist:lr:{session_id}", page, total)
    buttons = [[(f"Раунд {r.round_no}: игроки и набор"[:55], f"hist:rd:{r.id}")] for r in rows if r.id]
    buttons += ([nav] if nav else []) + [back]
    return Screen(
        f"Игры лобби (страница {page + 1} из {pages})\n\n" + "\n\n".join(blocks), buttons
    )
