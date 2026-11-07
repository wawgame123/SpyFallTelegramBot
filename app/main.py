import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import BotCommand, MenuButtonCommands
from sqlalchemy.orm import sessionmaker

from app.health import PollingHealthMiddleware, health_path, monitor_health
from app.database.worker import run_db

from app.bot.handlers import admin as admin_handlers
from app.bot.handlers import history, local, menu, online
from app.bot.handlers import packs as packs_handlers
from app.bot.handlers import settings as settings_handlers
from app.bot.handlers import stats as stats_handlers
from app.bot.errors import register_error_handlers
from app.bot.emoji import ButtonStyleMiddleware
from app.bot.middlewares.normalize import NormalizeButtonsMiddleware
from app.bot.middlewares.navigation import KeepMenuMiddleware
from app.bot.middlewares.throttle import ThrottleMiddleware
from app.bot.middlewares.private import PrivateCallbacksMiddleware
from app.bot.middlewares.track import TrackScreensMiddleware
from app.bot.runtime import OnlineRuntime
from app.bot.recovery import EditRecoveryMiddleware
from app.bot.tracker import ScreenTracker
from app.core.config import Settings, load_settings
from app.core.console import start_console_control
from app.database.info import check_connection, ensure_mysql, mask_url
from app.database.pack_repository import PackRepository
from app.database.repository import LocalGameRepository
from app.database.session import make_session_factory
from app.services.admin_service import AdminService
from app.services.history_service import HistoryService
from app.services.pack_service import PackService
from app.services.room_service import RoomService
from app.services.settings_service import SettingsService
from app.services.stats_service import StatsService


def build_dispatcher(
    bot: Bot, bot_username: str, factory: sessionmaker, settings: Settings,
    anchor_file: Path | None = None,
) -> tuple[Dispatcher, OnlineRuntime]:
    pack_repo = PackRepository(factory)
    library = pack_repo.load_library()
    pack_service = PackService(factory, library)
    local_repo = LocalGameRepository(factory)
    user_settings = SettingsService(factory)
    runtime = OnlineRuntime(
        bot,
        RoomService(factory, library=library, pack_service=pack_service),
        bot_username,
        maintenance=[lambda: local_repo.purge_stale(30)],
        prefs_for=user_settings.prefs,
    )
    dp = Dispatcher(events_isolation=SimpleEventIsolation())
    register_error_handlers(dp)
    throttle = ThrottleMiddleware(0.25)
    dp.message.outer_middleware(throttle)
    dp.callback_query.outer_middleware(throttle)
    dp.callback_query.outer_middleware(PrivateCallbacksMiddleware())
    tracker = ScreenTracker(anchor_file)
    dp.callback_query.outer_middleware(TrackScreensMiddleware(tracker))
    dp.message.outer_middleware(KeepMenuMiddleware())
    dp.callback_query.outer_middleware(KeepMenuMiddleware())
    dp["tracker"] = tracker
    dp["repo"] = local_repo
    dp["runtime"] = runtime
    dp["library"] = library
    dp["settings"] = settings
    dp["factory"] = factory
    dp["admin"] = AdminService(factory)
    dp["history"] = HistoryService(factory)
    dp["packs"] = pack_service
    dp["stats"] = StatsService(factory)
    dp["user_settings"] = user_settings
    runtime.edit_recovery = EditRecoveryMiddleware()
    bot.session.middleware(runtime.edit_recovery)
    bot.session.middleware(ButtonStyleMiddleware(user_settings.prefs))
    dp.message.outer_middleware(NormalizeButtonsMiddleware())
    dp.include_router(admin_handlers.router)
    dp.include_router(packs_handlers.router)
    dp.include_router(history.router)
    dp.include_router(online.router)
    dp.include_router(stats_handlers.router)
    dp.include_router(settings_handlers.router)
    dp.include_router(menu.router)
    dp.include_router(local.router)
    return dp, runtime


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    health_path().unlink(missing_ok=True)
    settings = load_settings()
    if settings.admin_id is None or settings.admin_id <= 0:
        logging.warning("ADMIN_ID is not configured: administrator actions are disabled")
    ensure_mysql(settings.database_url)
    logging.info("База данных: %s", mask_url(settings.database_url))
    try:
        await run_db(check_connection, settings.database_url)
    except Exception as err:
        raise SystemExit(
            f"Не удалось подключиться к MySQL ({mask_url(settings.database_url)}): {err}\n"
            "Проверьте, что служба MySQL запущена, а логин и пароль в DATABASE_URL верны."
        ) from err
    factory = await run_db(make_session_factory, settings.database_url)
    session = AiohttpSession(proxy=settings.proxy_url) if settings.proxy_url else None
    bot = Bot(token=settings.bot_token, session=session)
    me = await bot.get_me()
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Меню"),
            BotCommand(command="rules", description="Правила"),
            BotCommand(command="settings", description="Настройки"),
            BotCommand(command="menu", description="Главное меню"),
            BotCommand(command="cancel", description="Отменить ввод и открыть меню"),
        ]
    )
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    logging.info(
        "Администратор: логин=%s, ID=%s", settings.admin_username, settings.admin_id or "не задан"
    )
    dp, runtime = await run_db(
        build_dispatcher,
        bot, me.username, factory, settings,
        anchor_file=Path(__file__).resolve().parents[1] / ".ui_anchors.json",
    )
    start_console_control()
    polling_health = PollingHealthMiddleware()
    bot.session.middleware(polling_health)
    ticker = asyncio.create_task(runtime.run_ticker(), name="room-timer")
    health = asyncio.create_task(
        monitor_health(factory, runtime, ticker, polling_health), name="health-monitor",
    )
    try:
        await dp.start_polling(bot)
    finally:
        ticker.cancel()
        health.cancel()
        await asyncio.gather(ticker, health, return_exceptions=True)
        health_path().unlink(missing_ok=True)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
