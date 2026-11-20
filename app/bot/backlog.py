import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramServerError
from aiogram.types import Update

log = logging.getLogger(__name__)


async def replay_pending_updates(bot: Bot, dispatcher: Dispatcher) -> None:
    """Replay startup messages and only the latest queued callback per player."""
    offset = None
    retry_delay = 1
    pending: list[Update] = []
    latest_buttons: dict[int, Update] = {}
    allowed_updates = dispatcher.resolve_used_update_types()

    while True:
        try:
            batch = await bot.get_updates(
                offset=offset, limit=100, timeout=0, allowed_updates=allowed_updates,
            )
        except TelegramRetryAfter as error:
            await asyncio.sleep(error.retry_after)
            continue
        except (TelegramNetworkError, TelegramServerError):
            log.warning("Не удалось получить очередь Telegram. Повтор через %s с.", retry_delay)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 10)
            continue
        if not batch:
            break
        retry_delay = 1
        offset = max(update.update_id for update in batch) + 1
        for update in batch:
            callback = update.callback_query
            if callback is None:
                pending.append(update)
                continue
            user_id = callback.from_user.id
            previous = latest_buttons.get(user_id)
            if previous is None or update.update_id > previous.update_id:
                latest_buttons[user_id] = update

    pending.extend(latest_buttons.values())
    for update in sorted(pending, key=lambda item: item.update_id):
        try:
            await dispatcher.feed_update(bot, update, startup_backlog=True)
        except Exception:
            log.exception("Ошибка обработки накопленного обновления %s", update.update_id)
