from app.database.worker import run_db
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from sqlalchemy.orm import sessionmaker
from aiogram.types import Message

from app.bot import history_view, pack_view, settings_view, stats_view
from app.bot.handlers.history import _markup as history_markup
from app.bot.keyboards.menu import (
    MENU_TEXT,
    online_text,
    RULES_TEXT,
    admin_lobby_menu,
    back_menu,
    local_choice_menu,
    online_menu,
)
from app.bot.keyboards.render import to_markup, to_plain_markup
from app.bot.keyboards.reply import (
    BTN_ADMIN,
    BTN_HISTORY,
    BTN_LOCAL,
    BTN_ONLINE,
    BTN_PACKS,
    BTN_PROFILE,
    BTN_RATING,
    BTN_ROOM,
    BTN_SETTINGS,
    main_reply_keyboard,
)
from app.bot.runtime import OnlineRuntime
from app.bot.tracker import ScreenTracker
from app.bot.ui import clear_tracked, delete_quietly, present
from app.core.config import Settings
from app.core.security import is_admin
from app.database.info import describe, format_info
from app.database.repository import LocalGameRepository
from app.game.session import LocalSession
from app.services.pack_service import PackService
from app.services.settings_service import SettingsService
from app.services.stats_service import StatsService

log = logging.getLogger(__name__)
router = Router()


def _admin(settings: Settings, message: Message) -> bool:
    return is_admin(settings, message.from_user.id, message.from_user.username)


async def _cleanup(message: Message) -> None:
    try:
        await message.delete()
    except TelegramBadRequest:
        pass


@router.message(Command("menu", "cancel"), F.chat.type == "private")
@router.message(CommandStart(), F.chat.type == "private")
async def on_start(
    message: Message,
    bot: Bot,
    settings: Settings,
    state: FSMContext,
    tracker: ScreenTracker,
    repo: LocalGameRepository,
    runtime: OnlineRuntime,
) -> None:
    await state.clear()
    await clear_tracked(bot, message.chat.id, message.from_user.id, tracker)
    await _cleanup(message)
    previous = tracker.pop_anchor(message.from_user.id)
    if previous:
        await delete_quietly(bot, message.chat.id, previous)
    sent = await message.answer(MENU_TEXT, reply_markup=main_reply_keyboard(_admin(settings, message)))
    tracker.set_anchor(message.from_user.id, sent.message_id)


@router.message(Command("rules"), F.chat.type == "private")
async def on_rules_command(message: Message, state: FSMContext, tracker: ScreenTracker) -> None:
    await state.clear()
    await _cleanup(message)
    await present(message, tracker, RULES_TEXT, back_menu())


@router.message(Command("whoami"), F.chat.type == "private")
async def on_whoami(message: Message, settings: Settings) -> None:
    user = message.from_user
    admin = "да" if _admin(settings, message) else "нет"
    username = f"@{user.username}" if user.username else "не задан"
    await message.answer(f"Ваш ID: {user.id}\nЛогин: {username}\nАдминистратор: {admin}")


@router.message(F.text == BTN_ONLINE, F.chat.type == "private")
async def on_online(
    message: Message,
    runtime: OnlineRuntime,
    settings: Settings,
    state: FSMContext,
    tracker: ScreenTracker,
) -> None:
    await state.clear()
    await _cleanup(message)
    await present(
        message,
        tracker,
        online_text(*(await run_db(runtime.service.ban_status, message.from_user.id))),
        online_menu((await run_db(runtime.in_room, message.from_user.id)), _admin(settings, message)),
    )


@router.message(F.text == BTN_LOCAL, F.chat.type == "private")
async def on_local(
    message: Message,
    repo: LocalGameRepository,
    packs: PackService,
    state: FSMContext,
    tracker: ScreenTracker,
) -> None:
    await state.clear()
    await _cleanup(message)
    uid = message.from_user.id
    if (await run_db(repo.load, uid)) is not None:
        await present(
            message,
            tracker,
            "У вас есть незавершённая игра на одном устройстве.",
            local_choice_menu(),
        )
        return
    await clear_tracked(message.bot, message.chat.id, uid, tracker)
    session = LocalSession(library=(await run_db(packs.library_for, uid)))
    screen = session.screen()
    sent = await message.answer(screen.text, reply_markup=to_markup(screen, session.seq))
    session.message_id = sent.message_id
    (await run_db(repo.save, uid, session.to_dict()))


@router.message(F.text == BTN_HISTORY, F.chat.type == "private")
async def on_history(message: Message, state: FSMContext, tracker: ScreenTracker) -> None:
    await state.clear()
    await _cleanup(message)
    screen = history_view.menu_screen()
    await present(message, tracker, screen.text, history_markup(screen))


@router.message(F.text == BTN_ROOM, F.chat.type == "private")
async def on_room(
    message: Message, runtime: OnlineRuntime, state: FSMContext, tracker: ScreenTracker
) -> None:
    await state.clear()
    await _cleanup(message)
    uid = message.from_user.id
    room_id = (await run_db(runtime.service.room_id_of, uid))
    if room_id is None:
        await present(message, tracker, "Вы сейчас не в комнате. Нажмите «Играть онлайн».", back_menu())
        return
    await clear_tracked(message.bot, message.chat.id, uid, tracker)
    panel = await message.answer("Подключаюсь к комнате...")
    async with runtime.lock(room_id):
        await runtime.adopt_panel(room_id, uid, panel.message_id)
        await runtime.refresh(room_id)


async def _show_admin(
    message: Message, settings: Settings, state: FSMContext, tracker: ScreenTracker
) -> None:
    await state.clear()
    await _cleanup(message)
    if not _admin(settings, message):
        return
    await present(
        message,
        tracker,
        "Администрирование.\n\nТестовые лобби позволяют начать раунд с 1 или 2 игроками "
        "и выбрать свою роль. Жалобы: разбор публичных наборов.\n\n"
        "Команды:\n"
        "/ban ID [срок] - заблокировать игрока (30, 2ч, 7д или навсегда)\n"
        "/unban ID [сброс] - разблокировать\n"
        "/bans - список блокировок\n"
        "/packs [текст] - найти наборы\n"
        "/delpack НОМЕР - удалить набор",
        admin_lobby_menu(),
    )


@router.message(F.text == BTN_ADMIN, F.chat.type == "private")
async def on_admin_button(
    message: Message, settings: Settings, state: FSMContext, tracker: ScreenTracker
) -> None:
    await _show_admin(message, settings, state, tracker)


@router.message(Command("admin"), F.chat.type == "private")
async def on_admin_command(
    message: Message, settings: Settings, state: FSMContext, tracker: ScreenTracker
) -> None:
    await _show_admin(message, settings, state, tracker)


@router.message(F.text == BTN_PACKS, F.chat.type == "private")
async def on_packs(message: Message, state: FSMContext, tracker: ScreenTracker) -> None:
    await state.clear()
    await _cleanup(message)
    screen = pack_view.menu_screen()
    await present(message, tracker, screen.text, to_plain_markup(screen))


@router.message(F.text == BTN_PROFILE, F.chat.type == "private")
async def on_profile(
    message: Message, stats: StatsService, state: FSMContext, tracker: ScreenTracker
) -> None:
    await state.clear()
    await _cleanup(message)
    name = message.from_user.first_name or message.from_user.username or "Игрок"
    screen = stats_view.profile_screen((await run_db(stats.profile, message.from_user.id)), name)
    await present(message, tracker, screen.text, to_plain_markup(screen))


@router.message(F.text == BTN_RATING, F.chat.type == "private")
async def on_rating(
    message: Message, stats: StatsService, state: FSMContext, tracker: ScreenTracker
) -> None:
    await state.clear()
    await _cleanup(message)
    screen = stats_view.rating_screen((await run_db(stats.leaderboard, "points")), "points", message.from_user.id)
    await present(message, tracker, screen.text, to_plain_markup(screen))


async def _show_settings(
    message: Message, user_settings: SettingsService, state: FSMContext, tracker: ScreenTracker
) -> None:
    await state.clear()
    await _cleanup(message)
    screen = settings_view.settings_screen((await run_db(user_settings.prefs, message.from_user.id)))
    await present(message, tracker, screen.text, to_plain_markup(screen))


@router.message(F.text == BTN_SETTINGS, F.chat.type == "private")
async def on_settings_button(
    message: Message, user_settings: SettingsService, state: FSMContext, tracker: ScreenTracker
) -> None:
    await _show_settings(message, user_settings, state, tracker)


@router.message(Command("settings"), F.chat.type == "private")
async def on_settings_command(
    message: Message, user_settings: SettingsService, state: FSMContext, tracker: ScreenTracker
) -> None:
    await _show_settings(message, user_settings, state, tracker)


@router.message(Command("dbinfo"), F.chat.type == "private")
async def on_dbinfo(message: Message, settings: Settings, factory: sessionmaker) -> None:
    await _cleanup(message)
    if not _admin(settings, message):
        return
    info = await run_db(describe, factory, settings.database_url)
    await message.answer(format_info(info))
