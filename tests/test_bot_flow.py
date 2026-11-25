from app.views.telegram.emoji import strip_decor
from tests.pack_fixtures import DEFAULT_PACKS
import asyncio
import itertools
import time
from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageText,
    GetMe,
    GetUserProfilePhotos,
    SendDocument,
    SendMessage,
)
from aiogram.types import Chat, Message, User, UserProfilePhotos

from app.controllers.telegram.errors import register_error_handlers
from app.controllers.telegram.handlers import history, local, menu, online, packs, settings, stats
from app.controllers.telegram.middlewares.track import TrackScreensMiddleware
from app.controllers.telegram.middlewares.private import PrivateCallbacksMiddleware
from app.controllers.telegram.runtime import OnlineRuntime
from app.views.telegram.tracker import ScreenTracker
from app.core.config import Settings
from app.models.database.pack_repository import PackRepository
from app.models.database.repository import LocalGameRepository
from app.models.database.session import make_session_factory
from app.models.services.history_service import HistoryService
from app.models.services.pack_service import PackService
from app.models.services.settings_service import SettingsService
from app.models.services.room_service import RoomService
from app.models.services.stats_service import StatsService


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.fail_on = {}
        self.sent_ids = []
        self._ids = itertools.count(1000)

    async def close(self):
        return None

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if type(method) in self.fail_on:
            raise self.fail_on[type(method)](method)
        if isinstance(method, (SendMessage, EditMessageText, SendDocument)):
            chat_id = getattr(method, "chat_id", 0)
            message_id = getattr(method, "message_id", None) or next(self._ids)
            if isinstance(method, SendMessage):
                self.sent_ids.append(message_id)
            return Message(
                message_id=message_id,
                date=datetime.now(),
                chat=Chat(id=int(chat_id) if str(chat_id).lstrip("-").isdigit() else 0, type="private"),
                text=getattr(method, "text", None),
            )
        if isinstance(method, GetMe):
            return User(id=1, is_bot=True, first_name="bot", username="testbot")
        if isinstance(method, GetUserProfilePhotos):
            return UserProfilePhotos(total_count=0, photos=[])
        return True

    def of(self, kind):
        return [c for c in self.calls if isinstance(c, kind)]


class Env:
    def __init__(self, tmp_path):
        factory = make_session_factory(f"sqlite:///{tmp_path}/flow.db")
        repo = PackRepository(factory)
        repo.seed(DEFAULT_PACKS)
        library = repo.load_library()
        self.packs = PackService(factory, library)
        self.session = FakeSession()
        self.bot = Bot(token="123456:TESTTOKEN", session=self.session)
        self.service = RoomService(factory, library=library, pack_service=self.packs)
        runtime = OnlineRuntime(self.bot, self.service, "testbot")
        self.runtime = runtime
        self.local_repo = LocalGameRepository(factory)
        self.dp = Dispatcher()
        register_error_handlers(self.dp)
        self.tracker = ScreenTracker()
        self.dp.callback_query.outer_middleware(PrivateCallbacksMiddleware())
        self.dp.callback_query.outer_middleware(TrackScreensMiddleware(self.tracker))
        self.dp["tracker"] = self.tracker
        self.dp["factory"] = factory
        self.dp["repo"] = self.local_repo
        self.dp["runtime"] = runtime
        self.dp["library"] = library
        self.dp["settings"] = Settings(bot_token="x", database_url="x", admin_id=1)
        self.dp["history"] = HistoryService(factory)
        self.dp["packs"] = self.packs
        self.dp["stats"] = StatsService(factory)
        self.user_settings = SettingsService(factory)
        self.dp["user_settings"] = self.user_settings
        for router in (packs.router, history.router, online.router, stats.router, settings.router, menu.router, local.router):
            router._parent_router = None
            self.dp.include_router(router)
        self.counter = itertools.count(1)

    def _user(self, uid, username):
        return {"id": uid, "is_bot": False, "first_name": f"Имя{uid}", "username": username}

    async def text(self, uid, text, username=None):
        username = username or f"user{uid}"
        message = {
            "message_id": next(self.counter) + 10,
            "date": int(time.time()),
            "chat": {"id": uid, "type": "private"},
            "from": self._user(uid, username),
            "text": text,
        }
        if text.startswith("/"):
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        await self.dp.feed_raw_update(self.bot, {"update_id": next(self.counter), "message": message})

    async def press(self, uid, data, username=None, message_id=500):
        username = username or f"user{uid}"
        update = {
            "update_id": next(self.counter),
            "callback_query": {
                "id": str(next(self.counter)),
                "from": self._user(uid, username),
                "chat_instance": "ci",
                "data": data,
                "message": {
                    "message_id": message_id,
                    "date": int(time.time()),
                    "chat": {"id": uid, "type": "private"},
                    "text": "панель",
                },
            },
        }
        await self.dp.feed_raw_update(self.bot, update)

    def last_text(self, kind=(SendMessage, EditMessageText)):
        calls = [c for c in self.session.calls if isinstance(c, kind)]
        return calls[-1].text if calls else None

    def alerts(self):
        return [c for c in self.session.of(AnswerCallbackQuery) if c.text]


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def run(coro):
    return asyncio.run(coro)


def test_start_sends_reply_keyboard_with_admin_row_only_for_admin(env):
    async def scenario():
        await env.text(1, "/start", username="WAWGAME")
        await env.text(2, "/start", username="someone")

    run(scenario())
    sends = env.session.of(SendMessage)
    labels = lambda m: [b.text for row in m.reply_markup.keyboard for b in row]
    assert "Админ" in labels(sends[0]) and "Админ" not in labels(sends[1])
    assert "Наборы слов" in labels(sends[1])


def test_reply_buttons_open_sections(env):
    async def scenario():
        for label in ("Наборы слов", "История игр", "Профиль", "Рейтинг", "/rules", "Играть онлайн"):
            await env.text(5, label)

    run(scenario())
    texts = [strip_decor(c.text) for c in env.session.of((SendMessage, EditMessageText))]
    assert any(t.startswith("Наборы слов") for t in texts)
    assert any(t.startswith("История игр") for t in texts)
    assert any(t.startswith("Профиль") for t in texts)
    assert any(t.startswith("Рейтинг") for t in texts)
    assert any(t.startswith("Правила") for t in texts)
    assert any(t.startswith("Онлайн-игра") for t in texts)
    assert env.session.of(DeleteMessage)


def test_create_pack_through_chat_flow_and_export(env):
    async def scenario():
        await env.press(1, "pk:new")
        await env.text(1, "Мой набор")
        await env.text(1, "кот, пёс; лиса\nсыр")
        await env.text(1, "кот")
        pack_id = env.packs.list_packs(1, "my")[0][0].id
        await env.press(1, f"pk:view:{pack_id}")
        await env.press(1, f"pk:exp:{pack_id}")
        return pack_id

    pack_id = run(scenario())
    info = env.packs.info(1, pack_id)
    assert info.title == "Мой набор" and info.words_count == 4 and not info.is_public
    docs = env.session.of(SendDocument)
    assert docs and docs[0].caption


def test_menu_button_clears_state_so_text_not_added(env):
    async def scenario():
        await env.press(1, "pk:new")
        await env.text(1, "Набор")
        pack_id = env.packs.list_packs(1, "my")[0][0].id
        await env.press(1, f"pk:add:{pack_id}")
        await env.text(1, "Профиль")
        await env.text(1, "лишнее слово")
        return pack_id

    pack_id = run(scenario())
    assert env.packs.info(1, pack_id).words_count == 0
    assert any("Профиль" in c.text for c in env.session.of(EditMessageText))


def test_pack_access_control_via_callbacks(env):
    async def scenario():
        pid = env.packs.create_pack(1, "А", "Секретный", ["слово"])
        await env.press(2, f"pk:view:{pid}")
        await env.press(2, f"pk:del:{pid}")
        await env.press(2, f"pk:delyes:{pid}")
        return pid

    pid = run(scenario())
    assert env.packs.info(1, pid).title == "Секретный"
    assert len(env.alerts()) >= 3


def test_pack_search_flow(env):
    async def scenario():
        await env.press(3, "pk:find:builtin")
        await env.text(3, "слон")

    run(scenario())
    assert "Животные" in " ".join(
        b.text for c in env.session.of(EditMessageText) if c.reply_markup for r in c.reply_markup.inline_keyboard for b in r
    )


def test_moderation_buttons_admin_only(env):
    async def scenario():
        await env.press(2, "pk:mod:0", username="someone")
        await env.text(2, "Админ", username="someone")
        await env.text(1, "Админ", username="WAWGAME")

    run(scenario())
    texts = [a.text for a in env.alerts()]
    assert "Недоступно" in texts
    sends = env.session.of(SendMessage)
    assert not any(c.chat_id == 2 for c in sends)
    assert any(c.chat_id == 1 and c.text.startswith("Администрирование") for c in sends)


def test_online_custom_room_counts_through_telegram_panel(env, monkeypatch):
    monkeypatch.setattr(env.runtime, "allow_chat_message", lambda *args: True)
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        room_id = env.service.room_id_of(1)
        panel = env.service.panel_id_of(1)
        await env.press(1, "rm:ui:settings", message_id=panel)
        await env.press(1, "rm:custom:max", message_id=panel)
        assert "Отправьте число игроков" in env.last_text()
        await env.text(1, "5")
        assert env.service.get(room_id).settings["max_players"] == 5
        await env.press(1, "rm:custom:rounds", message_id=panel)
        assert "Отправьте число раундов" in env.last_text()
        await env.text(1, "19")
        room = env.service.get(room_id)
        assert room.settings["rounds"] == 19 and not room.custom_rule
        await env.runtime.close()

    run(scenario())


def test_online_telegram_word_command_filters_the_guess_keyboard(env):
    async def scenario():
        await env.press(1, "online:test:1", username="WAWGAME", message_id=700)
        room_id = env.service.room_id_of(1)
        panel = env.service.panel_id_of(1)
        env.service.act(1, "cy:role")
        env.service.act(1, "th:animals")
        env.service.act(1, "start")
        env.service.act(1, "role")
        await env.press(1, "rm:gu", message_id=panel)
        await env.text(1, "/word ЕЖ")
        room = env.service.get(room_id)
        assert room.guess_ui["1"]["query"] == "ЕЖ"
        buttons = [label for row in room.view_for(1).rows for label, cmd in row if cmd.startswith("gp:")]
        assert buttons == ["ёж"]
        await env.runtime.close()

    run(scenario())


def test_online_room_flow_with_deeplink_and_panels(env):
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        room_id = env.service.room_id_of(1)
        token = env.service.get(room_id).token
        await env.text(2, f"/start join_{token}")
        return room_id

    room_id = run(scenario())
    room = env.service.get(room_id)
    assert [p["id"] for p in room.players] == [1, 2]
    chats = {c.chat_id for c in env.session.of(EditMessageText) if c.chat_id}
    assert {1, 2} <= chats


def test_room_text_not_your_turn_is_rejected_and_deleted(env):
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        await env.text(1, "привет")

    run(scenario())
    assert env.session.of(DeleteMessage)


def test_join_by_code_flow(env):
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        code = env.service.get(env.service.room_id_of(1)).code
        await env.press(2, "online:join")
        await env.text(2, "wrong1")
        await env.text(2, code.lower())
        return code

    run(scenario())
    assert env.service.room_id_of(2) == env.service.room_id_of(1)


def test_test_lobby_only_for_admin(env):
    async def scenario():
        await env.press(2, "online:test:1", username="someone")
        await env.press(1, "online:test:2", username="WAWGAME")

    run(scenario())
    assert env.service.room_id_of(2) is None
    room = env.service.get(env.service.room_id_of(1))
    assert room.is_test and room.settings["test_size"] == 2


def test_local_game_via_reply_button_and_names(env):
    async def scenario():
        await env.text(4, "Один телефон")
        local_state = env.local_repo.load(4)
        assert local_state is not None
        await env.press(4, "lg:0:n:3", message_id=local_state["message_id"])
        for name in ("Аня", "Боря", "Вика"):
            await env.text(4, name)

    run(scenario())
    state = env.local_repo.load(4)
    assert state["names"] == ["Аня", "Боря", "Вика"] and state["stage"] == "review"


def test_second_local_press_offers_continue(env):
    async def scenario():
        await env.text(4, "Один телефон")
        await env.text(4, "Один телефон")

    run(scenario())
    last = env.session.of(SendMessage)[-1]
    assert "незавершённая" in last.text
    assert [b.text for r in last.reply_markup.inline_keyboard for b in r] == ["Продолжить игру", "Начать новую", "Закрыть"]


def test_my_room_button_without_room(env):
    run(env.text(9, "Моя комната"))
    assert "не в комнате" in env.last_text()
