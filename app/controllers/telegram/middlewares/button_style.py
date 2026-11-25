from collections.abc import Callable

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup

from app.models.database.worker import run_db
from app.views.telegram.emoji import decorate_markup


class ButtonStyleMiddleware(BaseRequestMiddleware):
    def __init__(self, prefs_for: Callable[[int], object]):
        self._prefs_for = prefs_for

    async def __call__(self, make_request, bot, method):
        markup = getattr(method, "reply_markup", None)
        chat_id = getattr(method, "chat_id", None)
        if isinstance(chat_id, int) and isinstance(markup, (InlineKeyboardMarkup, ReplyKeyboardMarkup)):
            prefs = (await run_db(self._prefs_for, chat_id))
            if prefs.emoji or prefs.colors:
                method = method.model_copy(
                    update={"reply_markup": decorate_markup(markup, prefs.emoji, prefs.colors)}
                )
        return await make_request(bot, method)
