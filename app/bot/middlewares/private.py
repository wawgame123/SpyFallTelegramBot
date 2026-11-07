from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from app.bot.ui import safe_answer


class PrivateCallbacksMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, CallbackQuery):
            message = event.message
            if not isinstance(message, Message) or message.chat.type != "private" or message.chat.id != event.from_user.id:
                await safe_answer(event, "Откройте бота в личном чате.")
                return None
        return await handler(event, data)
