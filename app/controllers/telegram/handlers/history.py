from app.models.database.worker import run_db
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app.views.telegram import history_view as view
from app.views.telegram.ui import safe_answer
from app.models.game.session import Screen
from app.models.services.history_service import HistoryService
from app.models.services.settings_service import SettingsService

router = Router()


def _markup(screen: Screen) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=action) for label, action in row]
            for row in screen.rows
        ]
    )


async def _show(call: CallbackQuery, screen: Screen) -> None:
    try:
        await call.message.edit_text(
            screen.text, reply_markup=_markup(screen), parse_mode="HTML" if screen.html else None
        )
    except TelegramBadRequest as err:
        if "not modified" not in str(err):
            raise
    await safe_answer(call)


def _int(value: str, default: int = 0) -> int:
    return int(value) if len(value) <= 10 and value.isascii() and value.isdigit() else default


@router.callback_query(F.data == "hist:menu")
async def on_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show(call, view.menu_screen())


@router.callback_query(F.data.startswith("hist:games:"))
async def on_games(
    call: CallbackQuery, history: HistoryService, user_settings: SettingsService, state: FSMContext
) -> None:
    await state.clear()
    page = _int(call.data.split(":")[2])
    rows, total = (await run_db(history.user_rounds, call.from_user.id, page, view.PAGE_SIZE))
    tz = (await run_db(user_settings.tz_minutes, call.from_user.id))
    await _show(call, view.games_screen(rows, total, page, tz))


@router.callback_query(F.data.startswith("hist:lobbies:"))
async def on_lobbies(
    call: CallbackQuery, history: HistoryService, user_settings: SettingsService, state: FSMContext
) -> None:
    await state.clear()
    page = _int(call.data.split(":")[2])
    rows, total = (await run_db(history.user_lobbies, call.from_user.id, page, view.PAGE_SIZE))
    tz = (await run_db(user_settings.tz_minutes, call.from_user.id))
    await _show(call, view.lobbies_screen(rows, total, page, tz))


@router.callback_query(F.data.startswith("hist:lobby:"))
async def on_lobby(
    call: CallbackQuery, history: HistoryService, user_settings: SettingsService, state: FSMContext
) -> None:
    await state.clear()
    parts = call.data.split(":")
    session_id = _int(parts[2], -1)
    list_page = _int(parts[3]) if len(parts) > 3 else 0
    row = (await run_db(history.lobby, call.from_user.id, session_id))
    if row is None:
        await safe_answer(call, "История этого лобби недоступна", show_alert=True)
        return
    tz = await run_db(user_settings.tz_minutes, call.from_user.id)
    await _show(call, view.lobby_screen(row, tz, list_page))


@router.callback_query(F.data.startswith("hist:lr:"))
async def on_lobby_rounds(
    call: CallbackQuery, history: HistoryService, user_settings: SettingsService, state: FSMContext
) -> None:
    await state.clear()
    parts = call.data.split(":")
    session_id = _int(parts[2], -1)
    page = _int(parts[3]) if len(parts) > 3 else 0
    list_page = _int(parts[4]) if len(parts) > 4 else 0
    result = (await run_db(history.lobby_rounds, call.from_user.id, session_id, page, view.PAGE_SIZE))
    if result is None:
        await safe_answer(call, "История этого лобби недоступна", show_alert=True)
        return
    rows, total = result
    tz = (await run_db(user_settings.tz_minutes, call.from_user.id))
    await _show(call, view.lobby_rounds_screen(session_id, rows, total, page, tz, list_page))


from app.controllers.telegram.handlers.history_management import router as management_router
router.include_router(management_router)
