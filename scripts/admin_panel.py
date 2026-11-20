import os
import sys

from dotenv import load_dotenv

from app.database.info import check_connection, ensure_mysql, mask_url
from app.database.migrate import run_migrations
from app.database.session import make_session_factory
from app.services.admin_service import AdminError, AdminService, parse_duration

DEFAULT_URL = "mysql+pymysql://spy:spy@localhost:3306/spy_game?charset=utf8mb4"
EXIT_WORDS = {"0", "q", "quit", "exit", "выход", "в"}

MENU = """
=== Админ-панель «Шпион» ===
1. Список блокировок
2. Заблокировать игрока
3. Разблокировать игрока
4. Информация об игроке
5. Список пользовательских наборов (поиск)
6. Удалить набор слов
7. Наборы с жалобами или скрытые
0. Выход
"""


def ask_user_id(ask, say) -> int | None:
    raw = ask("Telegram ID игрока (Enter, чтобы отменить): ").strip()
    if not raw:
        return None
    if not raw.isdigit() or int(raw) <= 0:
        say("ID должен быть положительным числом.")
        return None
    return int(raw)


def ask_yes(ask, prompt: str) -> bool:
    return ask(f"{prompt} (д/н): ").strip().lower() in ("д", "да", "y", "yes")


def show_bans(admin: AdminService, say) -> None:
    rows = admin.list_bans()
    if not rows:
        say("Активных блокировок нет.")
        return
    for row in rows:
        say(
            f"ID {row.user_id}, {row.name}: осталось {row.left_text}, "
            f"причина: {row.reason_text}, нарушений: {row.strikes}"
        )


def do_ban(admin: AdminService, ask, say) -> None:
    user_id = ask_user_id(ask, say)
    if user_id is None:
        return
    raw = ask("Срок (например 30, 2ч, 7д или навсегда): ")
    try:
        minutes = parse_duration(raw)
    except ValueError as err:
        say(str(err))
        return
    label = "навсегда" if minutes is None else raw.strip()
    if not ask_yes(ask, f"Заблокировать игрока {user_id} на срок: {label}?"):
        say("Отменено.")
        return
    admin.ban(user_id, minutes)
    say(f"Игрок {user_id} заблокирован: {label}.")


def do_unban(admin: AdminService, ask, say) -> None:
    user_id = ask_user_id(ask, say)
    if user_id is None:
        return
    info = admin.user_info(user_id)
    if info["ban_seconds"] <= 0 and info["strikes"] == 0:
        say("У этого игрока нет блокировки и нарушений.")
        return
    reset = ask_yes(ask, f"Нарушений: {info['strikes']}. Обнулить счётчик нарушений?")
    admin.unban(user_id, reset_strikes=reset)
    say(f"Игрок {user_id} разблокирован" + (", счётчик нарушений обнулён." if reset else "."))


def show_user(admin: AdminService, ask, say) -> None:
    user_id = ask_user_id(ask, say)
    if user_id is None:
        return
    info = admin.user_info(user_id)
    from app.services.penalty import ban_message

    status = (
        ban_message(info["ban_seconds"], info["ban_reason"])
        if info["ban_seconds"] > 0
        else "блокировки нет"
    )
    say(f"ID {info['user_id']}, имя: {info['name']}")
    say(f"Статус: {status}")
    say(f"Нарушений: {info['strikes']}, сыграно раундов: {info['rounds']}")
    say(f"Своих наборов: {info['packs']}, сейчас в комнате: {'да' if info['in_room'] else 'нет'}")


def list_packs(admin: AdminService, ask, say) -> None:
    query = ask("Поиск по названию, автору или номеру (Enter, чтобы показать все): ")
    packs, total = admin.list_packs(query, limit=25)
    if not packs:
        say("Ничего не найдено.")
        return
    for pack in packs:
        say(pack.line())
    if total > len(packs):
        say(f"Показано {len(packs)} из {total}. Уточните поиск.")


def do_delete_pack(admin: AdminService, ask, say) -> None:
    raw = ask("Номер набора (Enter, чтобы отменить): ").strip()
    if not raw:
        return
    if not raw.isdigit():
        say("Номер должен быть числом.")
        return
    try:
        pack = admin.get_pack(int(raw))
    except AdminError as err:
        say(str(err))
        return
    say(pack.line())
    if ask("Для подтверждения введите УДАЛИТЬ: ").strip() != "УДАЛИТЬ":
        say("Отменено.")
        return
    admin.delete_pack(pack.id)
    say(f"Набор «{pack.title}» удалён вместе со словами, избранным и жалобами.")


def show_reported(admin: AdminService, ask, say) -> None:
    packs = admin.reported_packs()
    if not packs:
        say("Наборов с жалобами и скрытых нет.")
        return
    for pack in packs:
        say(pack.line())
    raw = ask("Номер набора для действия (Enter, чтобы вернуться): ").strip()
    if not raw.isdigit():
        return
    action = ask("1 вернуть в публичные и снять жалобы, 2 скрыть, 3 удалить: ").strip()
    try:
        if action == "1":
            admin.set_hidden(int(raw), False)
            say("Набор возвращён, жалобы сняты.")
        elif action == "2":
            admin.set_hidden(int(raw), True)
            say("Набор скрыт.")
        elif action == "3":
            pack = admin.get_pack(int(raw))
            if ask("Для подтверждения введите УДАЛИТЬ: ").strip() == "УДАЛИТЬ":
                admin.delete_pack(pack.id)
                say(f"Набор «{pack.title}» удалён.")
            else:
                say("Отменено.")
        else:
            say("Неизвестное действие.")
    except AdminError as err:
        say(str(err))


ACTIONS = {
    "1": lambda a, ask, say: show_bans(a, say),
    "2": do_ban,
    "3": do_unban,
    "4": show_user,
    "5": list_packs,
    "6": do_delete_pack,
    "7": show_reported,
}


def run_menu(admin: AdminService, ask=input, say=print) -> None:
    while True:
        say(MENU)
        choice = ask("Выберите пункт: ").strip().lower()
        if choice in EXIT_WORDS:
            say("До свидания.")
            return
        action = ACTIONS.get(choice)
        if action is None:
            say("Нет такого пункта.")
            continue
        try:
            action(admin, ask, say)
        except AdminError as err:
            say(str(err))


def main() -> int:
    load_dotenv()
    url = os.getenv("DATABASE_URL", DEFAULT_URL)
    try:
        ensure_mysql(url)
        check_connection(url)
        run_migrations(url)
        factory = make_session_factory(url)
    except Exception as err:
        print(f"Не удалось подключиться к базе данных: {err}")
        print(f"Подключение: {mask_url(url)}")
        return 1
    print(f"База данных: {mask_url(url)}")
    run_menu(AdminService(factory))
    return 0


if __name__ == "__main__":
    sys.exit(main())
