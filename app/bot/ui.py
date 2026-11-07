import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from app.bot.tracker import ScreenTracker
from app.bot.keyboards.menu import MENU_TEXT
from app.bot.keyboards.reply import main_reply_keyboard
from app.core.config import Settings
from app.core.security import is_admin

log = logging.getLogger(__name__)


async def delete_quietly(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramAPIError:
        pass


async def clear_tracked(bot: Bot, chat_id: int, user_id: int, tracker: ScreenTracker) -> None:
    old = tracker.pop(user_id)
    if old:
        await delete_quietly(bot, chat_id, old)


async def present(
    message: Message,
    tracker: ScreenTracker,
    text: str,
    markup=None,
    parse_mode: str | None = None,
) -> Message:
    old = tracker.get(message.from_user.id)
    if old:
        try:
            await message.bot.edit_message_text(
                text, chat_id=message.chat.id, message_id=old,
                reply_markup=markup, parse_mode=parse_mode,
            )
            return message.model_copy(update={"message_id": old, "text": text})
        except TelegramBadRequest as err:
            if "not modified" in str(err):
                return message.model_copy(update={"message_id": old, "text": text})
            if not any(reason in str(err).lower() for reason in (
                "message to edit not found", "message can't be edited",
            )):
                raise
            await clear_tracked(message.bot, message.chat.id, message.from_user.id, tracker)
    sent = await message.answer(text, reply_markup=markup, parse_mode=parse_mode)
    tracker.set(message.from_user.id, sent.message_id)
    return sent


async def safe_answer(call: CallbackQuery, text: str | None = None, show_alert: bool = False) -> None:
    try:
        await call.answer(text, show_alert=show_alert)
    except TelegramAPIError as err:
        log.warning("Не удалось ответить на нажатие: %s", err)


async def refresh_main_keyboard(call: CallbackQuery, tracker: ScreenTracker, settings: Settings) -> None:
    """Reply keyboards require a new message; replace only their neutral carrier.

    Delete the old carrier first, so deleting it cannot hide the new keyboard.
    Keep the inline settings screen and game panels untouched.
    """
    uid = call.from_user.id
    old = tracker.pop_anchor(uid)
    if old:
        await delete_quietly(call.bot, call.message.chat.id, old)
    sent = await call.bot.send_message(
        call.message.chat.id, MENU_TEXT,
        reply_markup=main_reply_keyboard(is_admin(settings, uid, call.from_user.username)),
        disable_notification=True,
    )
    tracker.set_anchor(uid, sent.message_id)
