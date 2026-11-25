import time
from collections import OrderedDict, deque
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Update
from app.views.telegram.ui import safe_answer

BURST_LIMIT = 4


class ThrottleMiddleware(BaseMiddleware):
    def __init__(self, interval: float = 1.0, max_tracked: int = 20000):
        self.interval = interval
        self.max_tracked = max(1, max_tracked)
        self._recent = OrderedDict()
        self._notified = OrderedDict()
        self._active_callbacks: dict[int, int] = {}

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
        recent = self._recent.get(key)
        if recent is None:
            recent = deque()
        while recent and now - recent[0] >= self.interval:
            recent.popleft()
        active = self._active_callbacks.get(user.id, 0) if callback else 0
        if len(recent) >= BURST_LIMIT or active >= BURST_LIMIT:
            # Do not turn a callback flood into an answerCallbackQuery flood.
            notified = self._notified.get(user.id)
            if callback and (notified is None or now - notified >= 1.0):
                self._put(self._notified, user.id, now)
                await safe_answer(callback, "Слишком часто. Повторите через секунду.")
            return None
        recent.append(now)
        self._put(self._recent, key, recent)
        if not callback:
            return await handler(event, data)
        self._active_callbacks[user.id] = active + 1
        try:
            return await handler(event, data)
        finally:
            remaining = self._active_callbacks[user.id] - 1
            if remaining:
                self._active_callbacks[user.id] = remaining
            else:
                self._active_callbacks.pop(user.id)
