from app.bot.emoji import strip_decor
import asyncio
import itertools
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Bot
from aiogram.methods import EditMessageText, SendMessage
from sqlalchemy import text

from app.bot.handlers import history, local, menu, online, packs, settings, stats
from app.core.config import Settings
from app.database.session import make_session_factory
from app.main import build_dispatcher, main
from scripts.db_check import run as check_db
from tests.test_bot_flow import FakeSession


def test_build_dispatcher_wires_everything_and_handles_updates(tmp_path):
    for router in (packs.router, online.router, history.router, stats.router, settings.router, menu.router, local.router):
        router._parent_router = None
    factory = make_session_factory(f"sqlite:///{tmp_path}/main.db")
    session = FakeSession()
    bot = Bot(token="123456:TESTTOKEN", session=session)
    dp, runtime = build_dispatcher(bot, "testbot", factory, Settings(bot_token="x", database_url="x"))
    ids = itertools.count(1)

    async def send(text, uid=42):
        message = {
            "message_id": next(ids) + 10,
            "date": int(time.time()),
            "chat": {"id": uid, "type": "private"},
            "from": {"id": uid, "is_bot": False, "first_name": "Тест", "username": "tester"},
            "text": text,
        }
        if text.startswith("/"):
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text)}]
        await dp.feed_raw_update(bot, {"update_id": next(ids), "message": message})
        await asyncio.sleep(0.3)

    async def scenario():
        await send("/start")
        await send("Наборы слов")
        await send("Профиль")

    asyncio.run(scenario())
    texts = [strip_decor(c.text) for c in session.of((SendMessage, EditMessageText))]
    assert any(t == "Меню" for t in texts)
    assert any(t.startswith("Наборы слов") for t in texts)
    assert any(t.startswith("Профиль") for t in texts)
    assert runtime.service is not None


def test_main_initializes_sqlite_with_the_normal_entry_point(tmp_path, monkeypatch):
    db_path = tmp_path / "spy_game.sqlite3"
    url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("BOT_TOKEN", "123456:TESTTOKEN")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("PROXY_URL", "")
    monkeypatch.setattr("app.main.health_path", lambda: tmp_path / "health.json")
    monkeypatch.setattr("app.main.start_console_control", lambda: None)
    mocked_bot = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="testbot")),
        set_my_commands=AsyncMock(),
        set_chat_menu_button=AsyncMock(),
    )
    monkeypatch.setattr("app.main.Bot", lambda **kwargs: mocked_bot)
    monkeypatch.setattr("app.main.build_dispatcher", lambda *args, **kwargs: (None, None))

    async def check_startup(bot, dp, runtime, factory):
        assert bot is mocked_bot
        try:
            with factory() as db:
                assert db.scalar(text("SELECT version_num FROM alembic_version")) == "0010"
                assert db.scalar(text("SELECT COUNT(*) FROM question_packs")) == 0
                assert db.scalar(text("PRAGMA foreign_keys")) == 1
        finally:
            factory.kw["bind"].dispose()

    fake_poll = AsyncMock(side_effect=check_startup)
    monkeypatch.setattr("app.main.poll", fake_poll)
    asyncio.run(main())
    fake_poll.assert_awaited_once()
    assert "Файл:" in check_db(url)
