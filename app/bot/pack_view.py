from app.game.session import Screen
from app.services.pack_service import MIN_WORDS, REPORT_REASONS, PackInfo

PAGE_SIZE = 6
WORDS_PAGE = 10
SCOPE_TITLES = {
    "my": "Мои наборы",
    "builtin": "Готовые наборы",
    "fav": "Избранное",
    "public": "Публичная библиотека",
}
SORT_LABELS = {"new": "новые", "name": "по названию", "size": "по размеру"}
SORT_ORDER = ["new", "name", "size"]


def _pages(total: int, size: int) -> int:
    return max(1, -(-total // size))


def _nav(prefix: str, page: int, total: int, size: int) -> list[tuple[str, str]]:
    nav = []
    if page > 0:
        nav.append(("Назад", f"{prefix}:{page - 1}"))
    if page < _pages(total, size) - 1:
        nav.append(("Вперёд", f"{prefix}:{page + 1}"))
    return nav


def menu_screen() -> Screen:
    return Screen(
        "Наборы слов\n\nВыберите раздел.",
        [
            [("Мои наборы", "pk:list:my:0"), ("Готовые наборы", "pk:list:builtin:0")],
            [("Избранное", "pk:list:fav:0"), ("Публичная библиотека", "pk:list:public:0")],
            [("Создать набор", "pk:new"), ("Импорт набора", "pk:import")],
            [("Закрыть", "menu:home")],
        ],
    )


def prompt_screen(text: str, back: str = "pk:menu") -> Screen:
    return Screen(text, [[("Отмена", back)]])


def list_screen(
    scope: str,
    infos: list[PackInfo],
    total: int,
    page: int,
    query: str | None = None,
    sort: str = "new",
    category_title: str | None = None,
) -> Screen:
    filters = []
    if query:
        filters.append(f"поиск: {query}")
    if category_title:
        filters.append(f"категория: {category_title}")
    if sort != "new":
        filters.append(f"сортировка: {SORT_LABELS[sort]}")
    head = f"{SCOPE_TITLES[scope]} (найдено: {total})"
    if filters:
        head += "\nФильтры: " + "; ".join(filters)
    if not infos:
        empty = {
            "my": "У вас пока нет своих наборов. Создайте первый.",
            "fav": "В избранном пока пусто. Добавьте набор из карточки.",
        }.get(scope, "Ничего не найдено.")
        head += f"\n\n{empty}"
    rows: list[list[tuple[str, str]]] = []
    for info in infos:
        mark = ""
        if info.is_favorite:
            mark = "[избр.] "
        if scope == "my":
            mark += "[публ.] " if info.is_public else "[личн.] "
        rows.append([(f"{mark}{info.title} ({info.words_count})", f"pk:view:{info.id}")])
    nav = _nav(f"pk:list:{scope}", page, total, PAGE_SIZE)
    if nav:
        rows.append(nav)
    controls = [("Поиск", f"pk:find:{scope}"), (f"Сортировка: {SORT_LABELS[sort]}", f"pk:sort:{scope}")]
    rows.append(controls)
    if scope in ("builtin", "public"):
        rows.append([("Категория", f"pk:catlist:{scope}")])
    if query or category_title or sort != "new":
        rows.append([("Сбросить фильтры", f"pk:clear:{scope}")])
    if scope == "my":
        rows.append([("Создать набор", "pk:new")])
    rows.append([("К разделам", "pk:menu")])
    return Screen(head, rows)


def category_screen(scope: str, categories: list[tuple[int, str]]) -> Screen:
    rows = [[("Все категории", f"pk:cat:{scope}:0")]]
    rows += [[(title, f"pk:cat:{scope}:{cid}")] for cid, title in categories]
    rows.append([("Назад", f"pk:list:{scope}:0")])
    return Screen("Выберите категорию.", rows)


def back_target(info: PackInfo, viewer_id: int) -> str:
    if info.owner_id == viewer_id:
        return "pk:list:my:0"
    if info.is_builtin:
        return "pk:list:builtin:0"
    return "pk:list:public:0"


def card_screen(info: PackInfo, viewer_id: int) -> Screen:
    own = info.owner_id == viewer_id
    if info.is_builtin:
        author = "готовый набор"
    else:
        author = info.owner_name or "неизвестно"
    access = "личный"
    if info.is_hidden:
        access = "скрыт модерацией"
    elif info.is_public:
        access = "публичный"
    lines = [
        info.title,
        "",
        f"Автор: {author}",
        f"Категория: {info.category}",
        f"Слов: {info.words_count}",
    ]
    if not info.is_builtin:
        lines.append(f"Доступ: {access}")
    lines.append(f"В избранном: {'да' if info.is_favorite else 'нет'}")
    if info.description:
        lines += ["", info.description]
    if info.words_count == 0:
        lines += ["", "Набор пуст: в игре он недоступен."]
    elif info.words_count < MIN_WORDS:
        lines += ["", f"Слов мало: для комфортной игры нужно не менее {MIN_WORDS}."]
    fav = ("Убрать из избранного" if info.is_favorite else "В избранное", f"pk:fav:{info.id}")
    rows: list[list[tuple[str, str]]] = [[("Слова", f"pk:words:{info.id}:0")]]
    if own:
        rows[0].append(("Добавить слова", f"pk:add:{info.id}"))
        rows.append(
            [
                ("Переименовать", f"pk:ren:{info.id}"),
                ("Сделать личным" if info.is_public else "Сделать публичным", f"pk:vis:{info.id}"),
            ]
        )
    rows.append([("Копировать", f"pk:copy:{info.id}"), ("Экспорт", f"pk:exp:{info.id}")])
    rows.append([fav])
    if own:
        rows.append([("Удалить набор", f"pk:del:{info.id}")])
    elif not info.is_builtin and info.is_public:
        rows.append([("Пожаловаться", f"pk:rep:{info.id}")])
    rows.append([("Назад", back_target(info, viewer_id))])
    return Screen("\n".join(lines), rows)


def words_screen(
    info: PackInfo, words: list[tuple[int, str]], total: int, page: int, own: bool
) -> Screen:
    head = f"{info.title}: слова (всего {total}), страница {page + 1} из {_pages(total, WORDS_PAGE)}"
    rows: list[list[tuple[str, str]]] = []
    if not words:
        head += "\n\nВ наборе пока нет слов."
    elif own:
        head += "\n\nНажмите на слово, чтобы изменить или удалить его."
        for i in range(0, len(words), 2):
            rows.append([(w, f"pk:w:{wid}") for wid, w in words[i : i + 2]])
    else:
        head += "\n\n" + "\n".join(f"{page * WORDS_PAGE + i}. {w}" for i, (_, w) in enumerate(words, 1))
    nav = _nav(f"pk:words:{info.id}", page, total, WORDS_PAGE)
    if nav:
        rows.append(nav)
    if own:
        rows.append([("Добавить слова", f"pk:add:{info.id}")])
    rows.append([("К набору", f"pk:view:{info.id}")])
    return Screen(head, rows)


def word_screen(word_id: int, word: str, pack_id: int) -> Screen:
    return Screen(
        f"Слово: {word}",
        [
            [("Изменить", f"pk:wed:{word_id}"), ("Удалить", f"pk:wdel:{word_id}")],
            [("Назад", f"pk:words:{pack_id}:0")],
        ],
    )


def delete_screen(info: PackInfo) -> Screen:
    return Screen(
        f"Удалить набор «{info.title}» ({info.words_count} слов)?\n\nЭто действие нельзя отменить.",
        [[("Да, удалить", f"pk:delyes:{info.id}")], [("Отмена", f"pk:view:{info.id}")]],
    )


def report_screen(info: PackInfo) -> Screen:
    rows = [[(label, f"pk:repdo:{info.id}:{code}")] for code, label in REPORT_REASONS.items()]
    rows.append([("Отмена", f"pk:view:{info.id}")])
    return Screen(f"Пожаловаться на набор «{info.title}». Укажите причину.", rows)


def done_screen(text: str, pack_id: int) -> Screen:
    return Screen(text, [[("Готово", f"pk:view:{pack_id}")]])


def moderation_screen(items: list[PackInfo], total: int, page: int) -> Screen:
    if not items:
        return Screen("Жалоб нет.", [[("К разделам", "pk:menu")]])
    rows = []
    for info in items:
        mark = "[скрыт] " if info.is_hidden else ""
        rows.append([(f"{mark}{info.title}: жалоб {info.reports}", f"pk:modv:{info.id}")])
    nav = _nav("pk:mod", page, total, PAGE_SIZE)
    if nav:
        rows.append(nav)
    rows.append([("К разделам", "pk:menu")])
    return Screen(f"Жалобы на наборы (всего {total}).", rows)


def moderation_card(info: PackInfo, reasons: list[str]) -> Screen:
    text = (
        f"{info.title}\n\nАвтор: {info.owner_name or 'неизвестно'}\n"
        f"Слов: {info.words_count}\nСтатус: {'скрыт' if info.is_hidden else 'виден'}\n"
        f"Жалобы: {', '.join(reasons) if reasons else 'нет'}"
    )
    return Screen(
        text,
        [
            [("Оставить и снять жалобы", f"pk:modr:{info.id}")],
            [("Скрыть", f"pk:modh:{info.id}"), ("Удалить", f"pk:modd:{info.id}")],
            [("Назад", "pk:mod:0")],
        ],
    )
