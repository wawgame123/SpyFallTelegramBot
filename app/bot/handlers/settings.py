from app.database.worker import run_db
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.bot import settings_view
from app.bot.keyboards.render import to_plain_markup
from app.bot.ui import delete_quietly, refresh_main_keyboard, safe_answer
from app.bot.tracker import ScreenTracker
from app.core.config import Settings
from app.game.session import Screen
from app.services.settings_service import SettingsService

router = Router()


async def _edit(call: CallbackQuery, screen: Screen, tracker: ScreenTracker) -> None:
    uid = call.from_user.id
    anchor = tracker.get_anchor(uid)
    if anchor is not None and anchor > call.message.message_id:
        # Send below the keyboard carrier before deleting the old card, keeping
        # a usable screen if Telegram rejects sending the replacement.
        sent = await call.message.answer(
            screen.text, reply_markup=to_plain_markup(screen), disable_notification=True,
        )
        tracker.set(uid, sent.message_id)
        await delete_quietly(call.bot, call.message.chat.id, call.message.message_id)
        return
    try:
        await call.message.edit_text(screen.text, reply_markup=to_plain_markup(screen))
    except TelegramBadRequest as err:
        if "not modified" not in str(err):
            raise


@router.callback_query(F.data.startswith("set:"))
async def on_settings(
    call: CallbackQuery,
    user_settings: SettingsService,
    tracker: ScreenTracker,
    settings: Settings,
) -> None:
    parts = call.data.split(":")
    uid = call.from_user.id
    action = parts[1]
    if action == "tzmenu":
        await _edit(call, settings_view.timezone_screen((await run_db(user_settings.prefs, uid))), tracker)
        await safe_answer(call)
        return
    if action == "menu":
        await _edit(call, settings_view.settings_screen((await run_db(user_settings.prefs, uid))), tracker)
        await safe_answer(call)
        return
    if action == "noop":
        await safe_answer(call)
        return
    if action == "tzset" and len(parts) > 2:
        try:
            (await run_db(user_settings.set_tz_hours, uid, int(parts[2])))
        except ValueError:
            await safe_answer(call, "Недопустимый часовой пояс", show_alert=True)
            return
        await _edit(call, settings_view.timezone_screen((await run_db(user_settings.prefs, uid))), tracker)
        await safe_answer(call, "Часовой пояс сохранён")
        return
    if action in ("emoji", "colors") and len(parts) > 2:
        if parts[2] not in ("0", "1"):
            await safe_answer(call)
            return
        enabled = parts[2] == "1"
        previous = (await run_db(user_settings.prefs, uid))
        if action == "emoji":
            (await run_db(user_settings.set_emoji, uid, enabled))
        else:
            (await run_db(user_settings.set_colors, uid, enabled))
        changed = getattr(previous, action) != enabled
        if changed or tracker.get_anchor(uid) is None:
            await refresh_main_keyboard(call, tracker, settings)
        await _edit(call, settings_view.settings_screen((await run_db(user_settings.prefs, uid))), tracker)
        await safe_answer(call, "Сохранено")
        return
    await _edit(call, settings_view.settings_screen((await run_db(user_settings.prefs, uid))), tracker)
    await safe_answer(call)
