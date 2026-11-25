from app.views.telegram.emoji import strip_decor
import asyncio
import itertools
import time

from aiogram import Bot
from aiogram.methods import EditMessageText, SendMessage

from app.controllers.telegram.handlers import history, local, menu, online, packs, settings, stats
from app.core.config import Settings
from app.models.database.session import make_session_factory
from app.main import build_dispatcher
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
