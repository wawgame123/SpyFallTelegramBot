from app.models.database.worker import run_db
import time

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from app.views.telegram.keyboards.menu import admin_lobby_menu, online_menu, online_text
from app.views.telegram.keyboards.reply import ALL_BUTTONS
from app.controllers.telegram.runtime import OnlineRuntime
from app.views.telegram.tracker import ScreenTracker
from app.views.telegram.ui import clear_tracked, delete_quietly, present, safe_answer
from app.models.services.penalty import format_duration
from app.models.services.room_access import RoomPasswordRequired
from app.core.config import Settings
from app.core.security import is_admin
from app.models.game.room import RoomError

router = Router()
MESSAGE_COOLDOWN = 1.0


class JoinStates(StatesGroup):
    code = State()
    password = State()


class InRoom(Filter):
    async def __call__(self, message: Message, runtime: OnlineRuntime) -> bool:
        return (await run_db(runtime.in_room, message.from_user.id))


def display_name(user: User) -> str:
    return user.first_name or user.username or "Игрок"


async def _edit(call: CallbackQuery, text: str, markup) -> None:
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as err:
        if "not modified" not in str(err):
            raise


async def _safe_delete(message: Message) -> None:
    try:
        await message.delete()
    except TelegramBadRequest:
        pass


async def _enter_room(
    message: Message,
    runtime: OnlineRuntime,
    room_id: int,
    user_id: int,
    tracker: ScreenTracker,
    settings: Settings,
) -> None:
    await clear_tracked(message.bot, message.chat.id, user_id, tracker)
    panel = await message.answer("Подключаюсь к комнате…")
    await runtime.adopt_panel(room_id, user_id, panel.message_id)
    await runtime.refresh(room_id)


@router.message(CommandStart(deep_link=True), F.chat.type == "private")
async def on_deeplink(
    message: Message,
    command: CommandObject,
    runtime: OnlineRuntime,
    state: FSMContext,
    tracker: ScreenTracker,
    settings: Settings,
) -> None:
    args = command.args or ""
    if not args.startswith("join_"):
        raise SkipHandler
    await state.clear()
    await _safe_delete(message)
    uid = message.from_user.id
    try:
        update = (await run_db(runtime.service.join_token, uid, display_name(message.from_user), args[5:]))
    except RoomPasswordRequired as err:
        await state.set_state(JoinStates.password)
        await state.set_data({"invite": args[5:]})
        await present(message, tracker, f"{err}. Введите пароль сообщением.", online_menu_back())
        return
    except RoomError as err:
        await present(
            message, tracker, str(err),
            online_menu((await run_db(runtime.in_room, uid)), is_admin(settings, uid, message.from_user.username)),
        )
        return
    await _enter_room(message, runtime, update.room_id, uid, tracker, settings)


@router.callback_query(F.data == "menu:online")
async def on_online_menu(
    call: CallbackQuery, runtime: OnlineRuntime, state: FSMContext, settings: Settings
) -> None:
    await state.clear()
    admin = is_admin(settings, call.from_user.id, call.from_user.username)
    await _edit(
        call,
        online_text(*(await run_db(runtime.service.ban_status, call.from_user.id))),
        online_menu((await run_db(runtime.in_room, call.from_user.id)), admin, runtime.web_url),
    )
    await safe_answer(call)


@router.callback_query(F.data == "online:testmenu")
async def on_test_menu(call: CallbackQuery, settings: Settings) -> None:
    if not is_admin(settings, call.from_user.id, call.from_user.username):
        await safe_answer(call, "Недоступно", show_alert=True)
        return
    await _edit(
        call,
        "Тестовые лобби.\n\nПозволяют начать раунд с 1 или 2 игроками и выбрать свою роль.",
        admin_lobby_menu(),
    )
    await safe_answer(call)


@router.callback_query(F.data.startswith("online:test:"))
async def on_create_test(
    call: CallbackQuery, runtime: OnlineRuntime, settings: Settings
) -> None:
    uid = call.from_user.id
    if not is_admin(settings, uid, call.from_user.username):
        await safe_answer(call, "Недоступно", show_alert=True)
        return
    size = call.data.rsplit(":", 1)[1]
    if size not in ("1", "2"):
        await safe_answer(call)
        return
    try:
        update = (await run_db(runtime.service.create, uid, display_name(call.from_user), test_size=int(size)))
    except RoomError as err:
        await safe_answer(call, str(err), show_alert=True)
        return
    await runtime.adopt_panel(update.room_id, uid, call.message.message_id)
    await runtime.refresh(update.room_id)
    await safe_answer(call)


@router.callback_query(F.data == "online:create")
async def on_create(call: CallbackQuery, runtime: OnlineRuntime) -> None:
    uid = call.from_user.id
    try:
        update = await run_db(runtime.service.create, uid, display_name(call.from_user), listed=True)
    except RoomError as err:
        await safe_answer(call, str(err), show_alert=True)
        return
    await runtime.adopt_panel(update.room_id, uid, call.message.message_id)
    await runtime.refresh(update.room_id)
    await safe_answer(call)


@router.callback_query(F.data == "online:join")
async def on_join_prompt(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(JoinStates.code)
    await _edit(
        call,
        "Введите код комнаты (6 символов) сообщением.",
        online_menu_back(),
    )
    await safe_answer(call)


def online_menu_back():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="menu:online")],
            [InlineKeyboardButton(text="В меню", callback_data="menu:home")],
        ]
    )


@router.message(
    JoinStates.code,
    F.text,
    ~F.text.startswith("/"),
    ~F.text.in_(ALL_BUTTONS),
    F.chat.type == "private",
)
async def on_code(
    message: Message, runtime: OnlineRuntime, state: FSMContext, tracker: ScreenTracker, settings: Settings
) -> None:
    uid = message.from_user.id
    await _safe_delete(message)
    try:
        update = (await run_db(runtime.service.join_code, uid, display_name(message.from_user), message.text))
    except RoomPasswordRequired as err:
        await state.set_state(JoinStates.password)
        await state.set_data({"code": message.text})
        await present(message, tracker, f"{err}. Введите пароль сообщением.", online_menu_back())
        return
    except RoomError as err:
        await present(message, tracker, f"Ошибка: {err}\nВведите код ещё раз или отмените ввод.", online_menu_back())
        return
    await state.clear()
    await _enter_room(message, runtime, update.room_id, uid, tracker, settings)


@router.message(
    JoinStates.password, F.text, ~F.text.startswith("/"), ~F.text.in_(ALL_BUTTONS), F.chat.type == "private",
)
async def on_password(
    message: Message, runtime: OnlineRuntime, state: FSMContext, tracker: ScreenTracker, settings: Settings,
) -> None:
    await _safe_delete(message)
    uid = message.from_user.id
    data = await state.get_data()
    join = runtime.service.join_token if "invite" in data else runtime.service.join_code
    try:
        update = await run_db(
            join, uid, display_name(message.from_user), data.get("invite") or data.get("code"),
            password=message.text,
        )
    except RoomError as err:
        await present(message, tracker, f"{err}. Повторите ввод или нажмите «Отмена».", online_menu_back())
        return
    await state.clear()
    await _enter_room(message, runtime, update.room_id, uid, tracker, settings)


@router.callback_query(F.data == "online:resume")
async def on_resume(call: CallbackQuery, runtime: OnlineRuntime) -> None:
    uid = call.from_user.id
    room_id = (await run_db(runtime.service.room_id_of, uid))
    if room_id is None:
        await safe_answer(call, "Вы не в комнате", show_alert=True)
        await delete_quietly(call.bot, call.message.chat.id, call.message.message_id)
        return
    await runtime.adopt_panel(room_id, uid, call.message.message_id)
    await runtime.refresh(room_id)
    await safe_answer(call)


@router.callback_query(F.data == "rm:leave")
async def on_leave_request(call: CallbackQuery, runtime: OnlineRuntime) -> None:
    uid = call.from_user.id
    room_id = (await run_db(runtime.service.room_id_of, uid))
    if room_id is None:
        await safe_answer(call, "Вы не в комнате", show_alert=True)
        return
    try:
        async with runtime.lock(room_id):
            text = await run_db(
                runtime.service.leave_confirmation,
                uid, expected_room_id=room_id, expected_panel_id=call.message.message_id,
            )
    except RoomError as err:
        await safe_answer(call, str(err), show_alert=True)
        return
    pending = runtime.leave_confirmations.get(uid)
    if pending and (pending.room_id, pending.panel_id, pending.text) == (room_id, call.message.message_id, text):
        await safe_answer(call, "Подтвердите выход или нажмите «Отмена» в открытом сообщении.")
        return
    previous = runtime.leave_confirmations.pop(uid)
    if previous:
        await delete_quietly(call.bot, call.message.chat.id, previous.message_id)
    token = runtime.leave_confirmations.new_token()
    markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да, выйти", callback_data=f"rm_leave:confirm:{token}"),
        InlineKeyboardButton(text="Отмена", callback_data=f"rm_leave:cancel:{token}"),
    ]])
    sent = await call.bot.send_message(call.message.chat.id, text, reply_markup=markup)
    runtime.leave_confirmations.register(uid, room_id, call.message.message_id, sent.message_id, text, token)
    await safe_answer(call)


@router.callback_query(F.data.startswith("rm_leave:"))
async def on_leave_confirmation(
    call: CallbackQuery, runtime: OnlineRuntime, tracker: ScreenTracker
) -> None:
    uid = call.from_user.id
    parts = (call.data or "").split(":")
    if len(parts) != 3 or parts[1] not in ("confirm", "cancel"):
        await safe_answer(call, "Подтверждение устарело. Нажмите «Выйти» заново.", show_alert=True)
        return
    pending = runtime.leave_confirmations.consume(uid, call.message.message_id, parts[2])
    if pending is None:
        await safe_answer(call, "Подтверждение устарело. Нажмите «Выйти» заново.", show_alert=True)
        return
    if parts[1] == "cancel":
        await delete_quietly(call.bot, call.message.chat.id, pending.message_id)
        await safe_answer(call, "Выход отменён")
        return
    try:
        async with runtime.lock(pending.room_id):
            update = await run_db(
                runtime.service.act,
                uid, "leave_confirm", expected_room_id=pending.room_id,
                expected_panel_id=pending.panel_id, expected_leave_text=pending.text,
            )
    except RoomError as err:
        await delete_quietly(call.bot, call.message.chat.id, pending.message_id)
        await safe_answer(call, str(err), show_alert=True)
        return
    await delete_quietly(call.bot, call.message.chat.id, pending.message_id)
    await safe_answer(call, "Вы покинули комнату")
    if update.left_user:
        await delete_quietly(call.bot, call.message.chat.id, pending.panel_id)
        if update.penalty:
            strikes, minutes = update.penalty
            notice = await call.bot.send_message(
                call.message.chat.id,
                "Вы покинули игру во время раунда. Блокировка онлайн-игр: "
                f"{format_duration(minutes * 60)} (нарушение №{strikes}).",
            )
            tracker.set(uid, notice.message_id)
    if update.changed and update.room is not None:
        await runtime.refresh(pending.room_id)
    elif update.left_user:
        runtime.notify_room(pending.room_id)


@router.callback_query(F.data.startswith("rm:"))
async def on_room_action(
    call: CallbackQuery, runtime: OnlineRuntime, tracker: ScreenTracker
) -> None:
    uid = call.from_user.id
    action = call.data[3:]
    if action in ("leave", "leave_confirm"):
        await safe_answer(call, "Нажмите «Выйти» и подтвердите действие.", show_alert=True)
        return
    room_id = (await run_db(runtime.service.room_id_of, uid))
    if room_id is None:
        await safe_answer(call, "Вы не в комнате", show_alert=True)
        await delete_quietly(call.bot, call.message.chat.id, call.message.message_id)
        return
    update = None
    error = ""
    async with runtime.lock(room_id):
        try:
            update = await run_db(
                runtime.service.act,
                uid, action, expected_room_id=room_id, expected_panel_id=call.message.message_id,
            )
        except RoomError as err:
            error = str(err)
    if update is None:
        await safe_answer(call, error, show_alert=True)
        return
    await safe_answer(call, update.alert, show_alert=bool(update.alert))
    if update.left_user:
        await delete_quietly(call.bot, call.message.chat.id, call.message.message_id)
        if update.penalty:
            strikes, minutes = update.penalty
            try:
                notice = await call.bot.send_message(
                    call.message.chat.id,
                    "Вы покинули игру во время раунда. Блокировка онлайн-игр: "
                    f"{format_duration(minutes * 60)} (нарушение №{strikes}).",
                )
                tracker.set(call.from_user.id, notice.message_id)
            except Exception:
                pass
    if update.changed and update.room is not None:
        await runtime.refresh(room_id)
    elif update.left_user:
        runtime.notify_room(room_id)


@router.message(Command("word"), F.chat.type == "private", InRoom())
async def on_word_search(message: Message, command: CommandObject, runtime: OnlineRuntime) -> None:
    uid = message.from_user.id
    await _safe_delete(message)
    room_id = await run_db(runtime.service.room_id_of, uid)
    if room_id is None:
        return
    try:
        async with runtime.lock(room_id):
            await run_db(runtime.service.act, uid, "gq:" + (command.args or ""), expected_room_id=room_id)
    except RoomError as error:
        await runtime.warn_chat(message, str(error))
    else:
        await runtime.refresh(room_id)


@router.message(
    F.text,
    ~F.text.startswith("/"),
    ~F.text.in_(ALL_BUTTONS),
    F.chat.type == "private",
    InRoom(),
)
async def on_room_text(message: Message, runtime: OnlineRuntime) -> None:
    uid = message.from_user.id
    await _safe_delete(message)
    if not runtime.allow_chat_message(uid, MESSAGE_COOLDOWN):
        return
    room_id = (await run_db(runtime.service.room_id_of, uid))
    if room_id is None:
        return
    try:
        async with runtime.lock(room_id):
            await run_db(runtime.service.say, uid, message.text, expected_room_id=room_id, telegram_input=True)
    except RoomError as err:
        await runtime.warn_chat(message, str(err))
        return
    await runtime.refresh(room_id)
