from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from app.bot.emoji import strip_decor
from app.bot.keyboards.menu import MENU_TEXT
from app.bot.keyboards.reply import ALL_BUTTONS, main_reply_keyboard
from app.core.security import is_admin


class KeepMenuMiddleware(BaseMiddleware):
    """Keep a separate, permanent reply-keyboard carrier out of screen cleanup.

    Telegram clients may remove the keyboard when its message is deleted. The
    tracker is in memory, so restore the carrier on first navigation after restart.
    """

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        tracker = data.get("tracker")
        message = event.message if isinstance(event, CallbackQuery) else event
        if user is None or tracker is None or not isinstance(message, Message) or message.chat.type != "private":
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            payload = event.data or ""
            navigation = payload.startswith(("menu:", "online:", "set:", "pk:", "hist:", "stat:", "rm:", "rm_leave:", "lg:"))
        else:
            raw = message.text or ""
            text = strip_decor(raw)
            command = raw.split(maxsplit=1)[0].split("@")[0] if raw else ""
            # These handlers send the carrier themselves.
            deep_join = command == "/start" and len(raw.split(maxsplit=1)) > 1 and raw.split(maxsplit=1)[1].startswith("join_")
            if command in ("/start", "/menu", "/cancel") and not deep_join:
                return await handler(event, data)
            navigation = deep_join or text in ALL_BUTTONS or command in ("/settings", "/rules", "/admin")
        if navigation and tracker.get_anchor(user.id) is None:
            settings = data["settings"]
            sent = await message.bot.send_message(
                message.chat.id, MENU_TEXT,
                reply_markup=main_reply_keyboard(is_admin(settings, user.id, user.username)),
            )
            tracker.set_anchor(user.id, sent.message_id)
        return await handler(event, data)
