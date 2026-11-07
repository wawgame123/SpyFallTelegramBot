from app.game.session import Screen
from app.services.stats_service import MIN_GAMES_FOR_RATE, Profile, RatingRow

SORT_LABELS = {
    "points": "По очкам",
    "wins": "По победам",
    "spy": "Победы за шпиона",
    "rate": "Процент побед",
}


def profile_screen(profile: Profile | None, name: str) -> Screen:
    if profile is None:
        return Screen(
            f"Профиль: {name}\n\nВы ещё не сыграли ни одного онлайн-раунда.",
            [[("Закрыть", "menu:home")]],
        )
    text = (
        f"👤 Профиль · {name}\n\n"
        f"🎮 Сыграно раундов: {profile.games}\n"
        f"🏆 Победы: {profile.wins} ({profile.win_rate}%)\n"
        f"🧑 Победы обычным игроком: {profile.civilian_wins}\n"
        f"🕵️ Победы шпионом: {profile.spy_wins}\n"
        f"📉 Поражения: {profile.losses} · Ничьи: {profile.draws}\n"
        f"⭐ Всего очков: {profile.points}\n"
        f"🏠 Сыграно лобби: {profile.lobbies}"
    )
    return Screen(text, [[("Закрыть", "menu:home")]])


def rating_screen(rows: list[RatingRow], sort: str, viewer_id: int | None = None) -> Screen:
    title = f"🏆 Рейтинг · {SORT_LABELS[sort].lower()}"
    if not rows:
        note = (
            f"\n\nВ рейтинг попадают игроки, сыгравшие не менее {MIN_GAMES_FOR_RATE} раундов."
            if sort == "rate"
            else ""
        )
        body = f"Пока нет данных.{note}"
    else:
        lines = []
        for place, row in enumerate(rows, 1):
            mark = " (вы)" if row.user_id == viewer_id else ""
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(place, f"{place}.")
            lines.append(
                f"{medal} {row.name}{mark}: {row.points} оч., побед {row.wins} из {row.games}"
                f" ({row.win_rate}%), за шпиона {row.spy_wins}"
            )
        body = "\n".join(lines)
        if sort == "rate":
            body += f"\n\nУчитываются игроки с {MIN_GAMES_FOR_RATE}+ раундами."
    buttons = [
        [(label if key != sort else f"[x] {label}", f"stat:rating:{key}")]
        for key, label in SORT_LABELS.items()
    ]
    buttons.append([("Закрыть", "menu:home")])
    return Screen(f"{title}\n\n{body}", buttons)
