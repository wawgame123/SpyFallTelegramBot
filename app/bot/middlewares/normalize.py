from aiogram import BaseMiddleware
from aiogram.types import Message

from app.bot.emoji import strip_decor
from app.bot.keyboards.reply import ALL_BUTTONS


class NormalizeButtonsMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message) and event.text:
            stripped = strip_decor(event.text)
            if stripped != event.text and stripped in ALL_BUTTONS:
                event = event.model_copy(update={"text": stripped})
        return await handler(event, data)
