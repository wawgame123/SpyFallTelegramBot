"""Retry edits of menus/local games; online panels reload through OnlineRuntime."""
import asyncio
import logging
import time
from weakref import WeakValueDictionary

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramServerError, TelegramAPIError
from aiogram.methods import DeleteMessage, EditMessageText, EditMessageReplyMarkup

log = logging.getLogger(__name__)


class EditRecoveryMiddleware(BaseRequestMiddleware):
    def __init__(self):
        self.pending = {}
        self._locks = WeakValueDictionary()

    async def __call__(self, make_request, bot, method):
        if not isinstance(method, (DeleteMessage, EditMessageText, EditMessageReplyMarkup)):
            return await make_request(bot, method)
        key = (method.chat_id, method.message_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # A new edit or closing/deleting the screen supersedes its old retry.
            self.pending.pop(key, None)
            markup = getattr(method, 'reply_markup', None)
            online = markup and any((b.callback_data or '').startswith('rm:')
                                    for row in markup.inline_keyboard for b in row)
            try:
                return await make_request(bot, method)
            except (TelegramRetryAfter, TelegramNetworkError, TelegramServerError) as err:
                if not isinstance(method, DeleteMessage) and not online:
                    delay = err.retry_after if isinstance(err, TelegramRetryAfter) else 5
                    if len(self.pending) >= 4096:
                        self.pending.pop(next(iter(self.pending)))
                    self.pending[key] = (time.monotonic() + max(1, delay), method)
                raise

    async def retry_pending(self, bot):
        now = time.monotonic()
        due = [(key, entry) for key, entry in list(self.pending.items()) if entry[0] <= now][:4]
        async def retry(key, entry):
            if self.pending.get(key) is not entry:
                return
            # Re-enter preference decoration with the original undecorated edit.
            try:
                await bot(entry[1])
            except TelegramAPIError as err:
                log.warning('Deferred screen edit %s failed: %s', key, err)
        # Bound each cycle so an outage in many chats cannot starve game timers.
        await asyncio.gather(*(retry(key, entry) for key, entry in due))
