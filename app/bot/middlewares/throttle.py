import time
from collections import OrderedDict
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery
from app.bot.ui import safe_answer


class ThrottleMiddleware(BaseMiddleware):
    def __init__(self, interval: float = 0.25, max_tracked: int = 20000):
        self.interval = interval
        self.max_tracked = max(1, max_tracked)
        self._last = OrderedDict()
        self._notified = OrderedDict()

    def _put(self, entries, key, value):
        entries.pop(key, None)
        if len(entries) >= self.max_tracked:
            entries.popitem(last=False)
        entries[key] = value

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is None or self.interval <= 0:
            return await handler(event, data)
        now = time.monotonic()
        callback = isinstance(event, CallbackQuery)
        key = (user.id, "callback" if callback else "message")
        last = self._last.get(key)
        if last is not None and now - last < self.interval:
            # Do not turn a callback flood into an answerCallbackQuery flood.
            notified = self._notified.get(user.id)
            if callback and (notified is None or now - notified >= 1.0):
                self._put(self._notified, user.id, now)
                await safe_answer(event, "Слишком часто. Повторите через секунду.")
            return None
        self._put(self._last, key, now)
        return await handler(event, data)
