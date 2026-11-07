from app.database.worker import run_db
import io

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message, User

from app.bot import pack_view as view
from app.bot.keyboards.render import to_plain_markup
from app.bot.keyboards.reply import ALL_BUTTONS
from app.bot.tracker import ScreenTracker
from app.bot.ui import present, safe_answer
from app.core.config import Settings
from app.core.security import is_admin
from app.game.session import Screen
from app.services.pack_service import PackError, PackService, split_words

router = Router()
MAX_IMPORT_BYTES = 200 * 1024


class PackStates(StatesGroup):
    title = State()
    words = State()
    rename = State()
    find = State()
    edit_word = State()
    import_pack = State()


def display_name(user: User) -> str:
    return user.first_name or user.username or "Игрок"


async def _edit(call: CallbackQuery, screen: Screen) -> None:
    try:
        await call.message.edit_text(screen.text, reply_markup=to_plain_markup(screen))
    except TelegramBadRequest as err:
        if "not modified" not in str(err):
            raise


async def _list_screen(packs: PackService, uid: int, state: FSMContext, scope: str, page: int):
    data = await state.get_data()
    active = data.get("scope") == scope
    query = data.get("query") if active else None
    sort = data.get("sort", "new") if active else "new"
    cat = data.get("cat") if active else None
    infos, total = (await run_db(packs.list_packs, uid, scope, page, view.PAGE_SIZE, query, sort, cat))
    pages = max(1, -(-total // view.PAGE_SIZE))
    if page >= pages and page > 0:
        return await _list_screen(packs, uid, state, scope, pages - 1)
    cat_title = next((t for i, t in (await run_db(packs.categories)) if i == cat), None) if cat else None
    return view.list_screen(scope, infos, total, page, query, sort, cat_title)


async def _set_filters(state: FSMContext, scope: str, **changes) -> None:
    data = await state.get_data()
    if data.get("scope") != scope:
        data = {"scope": scope}
    data.update(changes)
    await state.set_data(data)


@router.callback_query(F.data.startswith("pk:"))
async def on_pack_action(
    call: CallbackQuery, state: FSMContext, packs: PackService, settings: Settings
) -> None:
    parts = call.data.split(":")
    action = parts[1]
    uid = call.from_user.id
    try:
        await _dispatch(call, state, packs, settings, action, parts[2:], uid)
    except PackError as err:
        await safe_answer(call, str(err), show_alert=True)


def _int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except ValueError:
        return default


async def _dispatch(call, state, packs, settings, action, args, uid) -> None:
    if action == "menu":
        await state.clear()
        await _edit(call, view.menu_screen())
        return await safe_answer(call)
    if action == "list":
        await state.set_state(None)
        scope, page = args[0], _int(args[1])
        await _edit(call, await _list_screen(packs, uid, state, scope, page))
        return await safe_answer(call)
    if action == "find":
        await state.set_state(PackStates.find)
        await state.update_data(find_scope=args[0])
        await _edit(call, view.prompt_screen("Введите текст для поиска по названиям и словам.", f"pk:list:{args[0]}:0"))
        return await safe_answer(call)
    if action == "sort":
        data = await state.get_data()
        current = data.get("sort", "new") if data.get("scope") == args[0] else "new"
        nxt = view.SORT_ORDER[(view.SORT_ORDER.index(current) + 1) % len(view.SORT_ORDER)]
        await _set_filters(state, args[0], sort=nxt)
        await _edit(call, await _list_screen(packs, uid, state, args[0], 0))
        return await safe_answer(call)
    if action == "catlist":
        await _edit(call, view.category_screen(args[0], (await run_db(packs.categories))))
        return await safe_answer(call)
    if action == "cat":
        await _set_filters(state, args[0], cat=_int(args[1]) or None)
        await _edit(call, await _list_screen(packs, uid, state, args[0], 0))
        return await safe_answer(call)
    if action == "clear":
        await state.set_data({})
        await _edit(call, await _list_screen(packs, uid, state, args[0], 0))
        return await safe_answer(call)
    if action == "view":
        await state.set_state(None)
        await _edit(call, view.card_screen((await run_db(packs.info, uid, _int(args[0]))), uid))
        return await safe_answer(call)
    if action == "words":
        pack_id, page = _int(args[0]), _int(args[1])
        info = (await run_db(packs.info, uid, pack_id))
        words, total = (await run_db(packs.list_words, uid, pack_id, page, view.WORDS_PAGE))
        await _edit(call, view.words_screen(info, words, total, page, info.owner_id == uid))
        return await safe_answer(call)
    if action == "w":
        await state.set_state(None)
        pack_id, word = (await run_db(packs.word, uid, _int(args[0])))
        await _edit(call, view.word_screen(_int(args[0]), word, pack_id))
        return await safe_answer(call)
    if action == "wed":
        pack_id, word = (await run_db(packs.word, uid, _int(args[0])))
        await state.set_state(PackStates.edit_word)
        await state.update_data(word_id=_int(args[0]), pack_id=pack_id)
        await _edit(call, view.prompt_screen(f"Введите новое значение для слова «{word}».", f"pk:w:{args[0]}"))
        return await safe_answer(call)
    if action == "wdel":
        pack_id = (await run_db(packs.delete_word, uid, _int(args[0])))
        info = (await run_db(packs.info, uid, pack_id))
        words, total = (await run_db(packs.list_words, uid, pack_id, 0, view.WORDS_PAGE))
        await _edit(call, view.words_screen(info, words, total, 0, True))
        return await safe_answer(call, "Слово удалено")
    if action == "new":
        await state.set_state(PackStates.title)
        await _edit(call, view.prompt_screen("Введите название нового набора."))
        return await safe_answer(call)
    if action == "add":
        pack_id = _int(args[0])
        (await run_db(packs.info, uid, pack_id))
        await state.set_state(PackStates.words)
        await state.update_data(pack_id=pack_id)
        await _edit(
            call,
            view.done_screen(
                "Отправьте слова сообщением: по одному в строке или через запятую. "
                "Можно отправлять несколько сообщений. Когда закончите, нажмите «Готово».",
                pack_id,
            ),
        )
        return await safe_answer(call)
    if action == "ren":
        pack_id = _int(args[0])
        await state.set_state(PackStates.rename)
        await state.update_data(pack_id=pack_id)
        await _edit(call, view.prompt_screen("Введите новое название набора.", f"pk:view:{pack_id}"))
        return await safe_answer(call)
    if action == "vis":
        pack_id = _int(args[0])
        info = (await run_db(packs.info, uid, pack_id))
        (await run_db(packs.set_public, uid, pack_id, not info.is_public))
        await _edit(call, view.card_screen((await run_db(packs.info, uid, pack_id)), uid))
        return await safe_answer(call, "Набор опубликован" if not info.is_public else "Набор стал личным")
    if action == "copy":
        new_id = (await run_db(packs.copy_pack, uid, display_name(call.from_user), _int(args[0])))
        await _edit(call, view.card_screen((await run_db(packs.info, uid, new_id)), uid))
        return await safe_answer(call, "Копия создана")
    if action == "exp":
        slug, text = (await run_db(packs.export_text, uid, _int(args[0])))
        await call.message.answer_document(
            BufferedInputFile(text.encode("utf-8"), filename=f"{slug}.txt"),
            caption="Файл набора. Его можно загрузить обратно через «Импорт набора».",
        )
        return await safe_answer(call)
    if action == "fav":
        pack_id = _int(args[0])
        state_now = (await run_db(packs.toggle_favorite, uid, pack_id))
        await _edit(call, view.card_screen((await run_db(packs.info, uid, pack_id)), uid))
        return await safe_answer(call, "Добавлено в избранное" if state_now else "Убрано из избранного")
    if action == "del":
        await _edit(call, view.delete_screen((await run_db(packs.info, uid, _int(args[0])))))
        return await safe_answer(call)
    if action == "delyes":
        (await run_db(packs.delete_pack, uid, _int(args[0])))
        await _edit(call, await _list_screen(packs, uid, state, "my", 0))
        return await safe_answer(call, "Набор удалён")
    if action == "rep":
        await _edit(call, view.report_screen((await run_db(packs.info, uid, _int(args[0])))))
        return await safe_answer(call)
    if action == "repdo":
        pack_id = _int(args[0])
        (await run_db(packs.report, uid, pack_id, args[1]))
        await _edit(call, view.card_screen((await run_db(packs.info, uid, pack_id)), uid))
        return await safe_answer(call, "Жалоба отправлена")
    if action == "import":
        await state.set_state(PackStates.import_pack)
        await _edit(
            call,
            view.prompt_screen(
                "Отправьте текстовый файл (.txt) или сообщение со словами. "
                "Первая строка вида «# Название» задаёт название набора. "
                "Слова разделяйте переводами строк, запятыми или точками с запятой."
            ),
        )
        return await safe_answer(call)
    if action in ("mod", "modv", "modr", "modh", "modd"):
        if not is_admin(settings, uid, call.from_user.username):
            return await safe_answer(call, "Недоступно", show_alert=True)
        if action == "mod":
            items, total = (await run_db(packs.moderation_list, _int(args[0]) if args else 0, view.PAGE_SIZE))
            await _edit(call, view.moderation_screen(items, total, _int(args[0]) if args else 0))
            return await safe_answer(call)
        pack_id = _int(args[0])
        if action == "modv":
            info, reasons = (await run_db(packs.moderation_info, pack_id))
            await _edit(call, view.moderation_card(info, reasons))
            return await safe_answer(call)
        (await run_db(packs.moderate, pack_id, {"modr": "restore", "modh": "hide", "modd": "delete"}[action]))
        items, total = (await run_db(packs.moderation_list, 0, view.PAGE_SIZE))
        await _edit(call, view.moderation_screen(items, total, 0))
        return await safe_answer(call, "Готово")
    await safe_answer(call)


async def _finish(message: Message) -> None:
    try:
        await message.delete()
    except TelegramBadRequest:
        pass


async def _say(message: Message, tracker: ScreenTracker, screen: Screen) -> None:
    await present(message, tracker, screen.text, to_plain_markup(screen))


TEXT_ONLY = (F.text, ~F.text.startswith("/"), ~F.text.in_(ALL_BUTTONS))


@router.message(StateFilter(PackStates.title), *TEXT_ONLY)
async def on_title(
    message: Message, state: FSMContext, packs: PackService, tracker: ScreenTracker
) -> None:
    await _finish(message)
    try:
        pack_id = (await run_db(packs.create_pack, message.from_user.id, display_name(message.from_user), message.text))
    except PackError as err:
        await _say(message, tracker, view.prompt_screen(f"Ошибка: {err}. Введите название ещё раз."))
        return
    await state.set_state(PackStates.words)
    await state.update_data(pack_id=pack_id)
    await _say(
        message,
        tracker,
        view.done_screen(
            f"Набор «{message.text.strip()}» создан и пока личный.\n\n"
            "Отправьте слова сообщением: по одному в строке или через запятую. "
            "Можно отправлять несколько сообщений. Когда закончите, нажмите «Готово».",
            pack_id,
        ),
    )


@router.message(StateFilter(PackStates.words), *TEXT_ONLY)
async def on_words(
    message: Message, state: FSMContext, packs: PackService, tracker: ScreenTracker
) -> None:
    data = await state.get_data()
    pack_id = data.get("pack_id")
    await _finish(message)
    try:
        added, skipped = (await run_db(packs.add_words, message.from_user.id, pack_id, split_words(message.text)))
        info = (await run_db(packs.info, message.from_user.id, pack_id))
    except PackError as err:
        await state.clear()
        await _say(message, tracker, Screen(f"Ошибка: {err}", [[("К разделам", "pk:menu")]]))
        return
    await _say(
        message,
        tracker,
        view.done_screen(
            f"{info.title}: добавлено слов {added}, пропущено (повторы или некорректные) {skipped}.\n"
            f"Всего в наборе: {info.words_count}. Можно отправить ещё.",
            pack_id,
        ),
    )


@router.message(StateFilter(PackStates.rename), *TEXT_ONLY)
async def on_rename(
    message: Message, state: FSMContext, packs: PackService, tracker: ScreenTracker
) -> None:
    data = await state.get_data()
    pack_id = data.get("pack_id")
    await _finish(message)
    try:
        (await run_db(packs.rename, message.from_user.id, pack_id, message.text))
    except PackError as err:
        await _say(message, tracker, view.prompt_screen(f"Ошибка: {err}. Введите название ещё раз.", f"pk:view:{pack_id}"))
        return
    await state.set_state(None)
    info = (await run_db(packs.info, message.from_user.id, pack_id))
    await _say(message, tracker, view.card_screen(info, message.from_user.id))


@router.message(StateFilter(PackStates.find), *TEXT_ONLY)
async def on_find(
    message: Message, state: FSMContext, packs: PackService, tracker: ScreenTracker
) -> None:
    data = await state.get_data()
    scope = data.get("find_scope", "public")
    await state.set_state(None)
    await _set_filters(state, scope, query=message.text.strip()[:60])
    await _finish(message)
    await _say(message, tracker, await _list_screen(packs, message.from_user.id, state, scope, 0))


@router.message(StateFilter(PackStates.edit_word), *TEXT_ONLY)
async def on_edit_word(
    message: Message, state: FSMContext, packs: PackService, tracker: ScreenTracker
) -> None:
    data = await state.get_data()
    await _finish(message)
    try:
        pack_id = (await run_db(packs.edit_word, message.from_user.id, data.get("word_id", 0), message.text))
    except PackError as err:
        await _say(message, tracker, view.prompt_screen(f"Ошибка: {err}. Введите слово ещё раз.", f"pk:w:{data.get('word_id', 0)}"))
        return
    await state.set_state(None)
    info = (await run_db(packs.info, message.from_user.id, pack_id))
    words, total = (await run_db(packs.list_words, message.from_user.id, pack_id, 0, view.WORDS_PAGE))
    await _say(message, tracker, view.words_screen(info, words, total, 0, True))


async def _import(
    message: Message, state: FSMContext, packs: PackService, tracker: ScreenTracker, text: str
) -> None:
    try:
        pack_id = (await run_db(packs.import_text, message.from_user.id, display_name(message.from_user), text))
    except PackError as err:
        await _say(message, tracker, view.prompt_screen(f"Ошибка: {err}"))
        return
    await state.set_state(None)
    info = (await run_db(packs.info, message.from_user.id, pack_id))
    card = view.card_screen(info, message.from_user.id)
    await _say(
        message,
        tracker,
        Screen(f"Набор импортирован. Добавлено слов: {info.words_count}.\n\n{card.text}", card.rows),
    )


@router.message(StateFilter(PackStates.import_pack), *TEXT_ONLY)
async def on_import_text(
    message: Message, state: FSMContext, packs: PackService, tracker: ScreenTracker
) -> None:
    await _finish(message)
    await _import(message, state, packs, tracker, message.text)


@router.message(StateFilter(PackStates.import_pack), F.document)
async def on_import_file(
    message: Message, state: FSMContext, packs: PackService, bot: Bot, tracker: ScreenTracker
) -> None:
    document = message.document
    if document.file_size and document.file_size > MAX_IMPORT_BYTES:
        await _say(message, tracker, view.prompt_screen("Файл слишком большой (максимум 200 КБ)."))
        return
    buffer = io.BytesIO()
    await bot.download(document, destination=buffer)
    await _finish(message)
    try:
        text = buffer.getvalue().decode("utf-8-sig")
    except UnicodeDecodeError:
        await _say(message, tracker, view.prompt_screen("Не удалось прочитать файл. Сохраните его в кодировке UTF-8."))
        return
    await _import(message, state, packs, tracker, text)
