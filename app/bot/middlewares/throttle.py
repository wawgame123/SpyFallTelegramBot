import time
from collections import OrderedDict
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Update
from app.bot.ui import safe_answer


class ThrottleMiddleware(BaseMiddleware):
    def __init__(self, interval: float = 0.25, max_tracked: int = 20000):
        self.interval = interval
        self.max_tracked = max(1, max_tracked)
        self._last = OrderedDict()
        self._notified = OrderedDict()
        self._last_button = OrderedDict()
        self._active_callbacks: set[int] = set()

    def _put(self, entries, key, value):
        entries.pop(key, None)
        if len(entries) >= self.max_tracked:
            entries.popitem(last=False)
        entries[key] = value

    async def __call__(self, handler, event, data):
        # At the update level, filter button presses before the FSM can queue them.
        if isinstance(event, Update):
            callback = event.callback_query
            if callback is None:
                return await handler(event, data)
        else:
            callback = event if isinstance(event, CallbackQuery) else None
        user = data.get("event_from_user")
        # Startup replay already keeps just the last button per player. Its timing
        # isn't the original arrival timing and should not affect live limits.
        if user is None or self.interval <= 0 or data.get("startup_backlog"):
            return await handler(event, data)
        now = time.monotonic()
        key = (user.id, "callback" if callback else "message")
        last = self._last.get(key)
        too_fast = last is not None and now - last < self.interval
        if callback:
            message = callback.message
            button = (
                user.id,
                message.chat.id if message else None,
                message.message_id if message else callback.inline_message_id,
                callback.data or callback.game_short_name,
            )
            previous = self._last_button.get(button)
            too_fast = (
                too_fast
                or user.id in self._active_callbacks
                or (previous is not None and now - previous < 1.0)
            )
        if too_fast:
            # Do not turn a callback flood into an answerCallbackQuery flood.
            notified = self._notified.get(user.id)
            if callback and (notified is None or now - notified >= 1.0):
                self._put(self._notified, user.id, now)
                await safe_answer(callback, "Слишком часто. Повторите через секунду.")
            return None
        self._put(self._last, key, now)
        if not callback:
            return await handler(event, data)
        self._put(self._last_button, button, now)
        self._active_callbacks.add(user.id)
        try:
            return await handler(event, data)
        finally:
            self._active_callbacks.discard(user.id)
