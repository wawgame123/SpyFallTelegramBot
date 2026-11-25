import logging

from aiogram import Dispatcher
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.filters import ExceptionTypeFilter, Filter
from aiogram.types import ErrorEvent

log = logging.getLogger(__name__)

HARMLESS_BAD_REQUESTS = (
    "query is too old",
    "message is not modified",
    "message to edit not found",
    "message can't be edited",
    "message to delete not found",
)


class HarmlessBadRequest(Filter):
    async def __call__(self, event: ErrorEvent) -> bool:
        text = str(event.exception).lower()
        return isinstance(event.exception, TelegramBadRequest) and any(
            marker in text for marker in HARMLESS_BAD_REQUESTS
        )


async def on_network_error(event: ErrorEvent) -> bool:
    log.warning("Проблема связи с Telegram, событие пропущено: %s", event.exception)
    return True


async def on_harmless_bad_request(event: ErrorEvent) -> bool:
    log.warning("Устаревшее действие, пропущено: %s", event.exception)
    return True


def register_error_handlers(dp: Dispatcher) -> None:
    dp.errors.register(
        on_network_error,
        ExceptionTypeFilter(TelegramNetworkError, TelegramRetryAfter, TelegramServerError),
    )
    dp.errors.register(on_harmless_bad_request, HarmlessBadRequest())
