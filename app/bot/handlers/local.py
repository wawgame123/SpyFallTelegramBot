from app.database.worker import run_db
import asyncio
from weakref import WeakValueDictionary

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards.menu import RULES_TEXT, back_menu
from app.bot.tracker import ScreenTracker
from app.bot.ui import delete_quietly, safe_answer
from app.bot.keyboards.render import to_markup
from app.bot.keyboards.reply import ALL_BUTTONS
from app.database.repository import LocalGameRepository
from app.game.packs import PackLibrary
from app.services.pack_service import PackService
from app.game.session import LocalSession

router = Router()
_locks = WeakValueDictionary()


def _lock(user_id: int) -> asyncio.Lock:
    return _locks.setdefault(user_id, asyncio.Lock())


def _load(
    repo: LocalGameRepository, library: PackLibrary, user_id: int
) -> LocalSession | None:
    data = repo.load(user_id)
    return LocalSession.from_dict(data, library=library) if data else None


async def _safe_edit(call: CallbackQuery, text: str, markup) -> None:
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as err:
        if "not modified" not in str(err):
            raise


@router.callback_query(F.data == "menu:home")
async def on_home(call: CallbackQuery, state: FSMContext, tracker: ScreenTracker) -> None:
    uid = call.from_user.id
    current = tracker.get(uid)
    if current is not None and current != call.message.message_id:
        await delete_quietly(call.bot, call.message.chat.id, call.message.message_id)
        await safe_answer(call)
        return
    await state.clear()
    tracker.discard(uid, call.message.message_id)
    await delete_quietly(call.bot, call.message.chat.id, call.message.message_id)
    await safe_answer(call)


@router.callback_query(F.data == "menu:rules")
async def on_rules(call: CallbackQuery) -> None:
    await _safe_edit(call, RULES_TEXT, back_menu())
    await safe_answer(call)


@router.callback_query(F.data == "menu:local")
async def on_new_local(
    call: CallbackQuery, repo: LocalGameRepository, packs: PackService, state: FSMContext
) -> None:
    await state.clear()
    async with _lock(call.from_user.id):
        previous = (await run_db(repo.load, call.from_user.id))
        if previous and previous.get("message_id") != call.message.message_id and previous.get("message_id"):
            await delete_quietly(call.bot, call.message.chat.id, previous["message_id"])
        session = LocalSession(library=(await run_db(packs.library_for, call.from_user.id)))
        session.message_id = call.message.message_id
        (await run_db(repo.save, call.from_user.id, session.to_dict()))
    await _safe_edit(call, session.screen().text, to_markup(session.screen(), session.seq))
    await safe_answer(call)


@router.callback_query(F.data == "menu:resume")
async def on_resume(
    call: CallbackQuery,
    repo: LocalGameRepository,
    packs: PackService,
    state: FSMContext,
) -> None:
    await state.clear()
    async with _lock(call.from_user.id):
        session = (await run_db(_load, repo, (await run_db(packs.library_for, call.from_user.id)), call.from_user.id))
        if session is None:
            await safe_answer(call, "Сохранённой игры нет", show_alert=True)
            await delete_quietly(call.bot, call.message.chat.id, call.message.message_id)
            return
        if session.message_id and session.message_id != call.message.message_id:
            await delete_quietly(call.bot, call.message.chat.id, session.message_id)
        session.neutralize()
        session.message_id = call.message.message_id
        (await run_db(repo.save, call.from_user.id, session.to_dict()))
    screen = session.screen()
    await _safe_edit(call, screen.text, to_markup(screen, session.seq))
    await safe_answer(call)


@router.callback_query(F.data.startswith("lg:"))
async def on_action(
    call: CallbackQuery,
    repo: LocalGameRepository,
    packs: PackService,
) -> None:
    try:
        _, seq, action = call.data.split(":", 2)
        seq_number = int(seq)
    except ValueError:
        await safe_answer(call)
        return
    async with _lock(call.from_user.id):
        session = (await run_db(_load, repo, (await run_db(packs.library_for, call.from_user.id)), call.from_user.id))
        if session is None:
            await safe_answer(call, "Игра не найдена", show_alert=True)
            await delete_quietly(call.bot, call.message.chat.id, call.message.message_id)
            return
        if session.message_id and session.message_id != call.message.message_id:
            await safe_answer(call, "Это старый экран. Откройте текущую игру.", show_alert=True)
            return
        response = session.act(seq_number, action)
        if response.exit:
            (await run_db(repo.delete, call.from_user.id))
        else:
            session.message_id = call.message.message_id
            (await run_db(repo.save, call.from_user.id, session.to_dict()))
        seq_now = session.seq
    if response.exit:
        await delete_quietly(call.bot, call.message.chat.id, call.message.message_id)
        await safe_answer(call)
        return
    if response.screen is not None:
        await _safe_edit(call, response.screen.text, to_markup(response.screen, seq_now))
    await safe_answer(call, response.alert, show_alert=bool(response.alert))


@router.message(
    F.text, ~F.text.startswith("/"), ~F.text.in_(ALL_BUTTONS), F.chat.type == "private"
)
async def on_text(
    message: Message, bot: Bot, repo: LocalGameRepository, packs: PackService, tracker: ScreenTracker
) -> None:
    if tracker.get(message.from_user.id) is not None:
        return
    async with _lock(message.from_user.id):
        session = (await run_db(_load, repo, (await run_db(packs.library_for, message.from_user.id)), message.from_user.id))
        if session is None:
            return
        response = session.handle_text(message.text)
        if response is None or response.screen is None:
            return
        (await run_db(repo.save, message.from_user.id, session.to_dict()))
        message_id = session.message_id
        seq_now = session.seq
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    if message_id is None:
        return
    try:
        await bot.edit_message_text(
            response.screen.text,
            chat_id=message.chat.id,
            message_id=message_id,
            reply_markup=to_markup(response.screen, seq_now),
        )
    except TelegramBadRequest as err:
        if "not modified" not in str(err):
            raise
