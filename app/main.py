import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import BotCommand, MenuButtonCommands
from sqlalchemy.orm import sessionmaker

from app.health import PollingHealthMiddleware, health_path, monitor_health
from app.models.database.worker import run_db
from app.controllers.telegram.backlog import replay_pending_updates

from app.controllers.telegram.handlers import admin as admin_handlers
from app.controllers.telegram.handlers import history, local, menu, online
from app.controllers.telegram.handlers import packs as packs_handlers
from app.controllers.telegram.handlers import settings as settings_handlers
from app.controllers.telegram.handlers import stats as stats_handlers
from app.controllers.telegram.errors import register_error_handlers
from app.controllers.telegram.middlewares.button_style import ButtonStyleMiddleware
from app.controllers.telegram.middlewares.normalize import NormalizeButtonsMiddleware
from app.controllers.telegram.middlewares.navigation import KeepMenuMiddleware
from app.controllers.telegram.middlewares.throttle import ThrottleMiddleware
from app.controllers.telegram.middlewares.private import PrivateCallbacksMiddleware
from app.controllers.telegram.middlewares.track import TrackScreensMiddleware
from app.controllers.telegram.runtime import OnlineRuntime
from app.controllers.telegram.recovery import EditRecoveryMiddleware
from app.views.telegram.tracker import ScreenTracker
from app.core.config import Settings, load_settings
from app.core.console import start_console_control
from app.models.database.info import check_connection, ensure_supported_database, mask_url
from app.models.database.migrate import run_migrations
from app.models.database.pack_repository import PackRepository
from app.models.database.repository import LocalGameRepository
from app.models.database.session import make_session_factory
from app.models.services.admin_service import AdminService
from app.models.services.history_service import HistoryService
from app.models.services.pack_service import PackService
from app.models.services.room_service import RoomService
from app.models.services.settings_service import SettingsService
from app.models.services.stats_service import StatsService
from app.controllers.web.server import start_web, web_origin


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
    throttle = ThrottleMiddleware()
    dp.message.outer_middleware(throttle)
    dp.update.outer_middleware.unregister(dp.fsm)
    dp.update.outer_middleware(throttle)
    dp.update.outer_middleware(dp.fsm)
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


async def poll(bot: Bot, dp: Dispatcher, runtime: OnlineRuntime, factory: sessionmaker) -> None:
    # Give queued actions a chance to update game state before expiring old turns
    # or removing inactive rooms and local games.
    runner = None
    try:
        await replay_pending_updates(bot, dp)
        if getattr(runtime, "web_url", None):
            runner = await start_web(
                runtime, runtime.web_url, bot.token, packs=dp["packs"], stats=dp["stats"], factory=factory,
            )
        polling_health = PollingHealthMiddleware()
        bot.session.middleware(polling_health)
        ticker = asyncio.create_task(runtime.run_ticker(), name="room-timer")
        health = asyncio.create_task(
            monitor_health(factory, runtime, ticker, polling_health), name="health-monitor",
        )
        try:
            await dp.start_polling(bot, close_bot_session=False)
        finally:
            ticker.cancel()
            health.cancel()
            await asyncio.gather(ticker, health, return_exceptions=True)
    finally:
        if runner:
            await runner.cleanup()
        health_path().unlink(missing_ok=True)
        await runtime.close()
        await bot.session.close()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    health_path().unlink(missing_ok=True)
    settings = load_settings()
    if settings.admin_id is None or settings.admin_id <= 0:
        logging.warning("ADMIN_ID is not configured: administrator actions are disabled")
    ensure_supported_database(settings.database_url)
    logging.info("База данных: %s", mask_url(settings.database_url))
    try:
        await run_db(check_connection, settings.database_url)
    except Exception as err:
        raise SystemExit(
            f"Не удалось подключиться к базе данных ({mask_url(settings.database_url)}): {err}\n"
            "Проверьте адрес MySQL и учётные данные или доступ к файлу SQLite."
        ) from err
    await run_db(run_migrations, settings.database_url)
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
    runtime.web_url = web_origin()
    start_console_control()
    await poll(bot, dp, runtime, factory)


if __name__ == "__main__":
    asyncio.run(main())
