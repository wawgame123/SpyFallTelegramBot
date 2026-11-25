from tests.pack_fixtures import test_library
import asyncio
import random

import pytest
from aiogram.methods import DeleteMessage, EditMessageText, SendMessage

from app.views.telegram import settings_view
from app.controllers.telegram.middlewares.track import TrackScreensMiddleware
from app.views.telegram.tracker import ScreenTracker
from app.models.game.packs import Pack, PackLibrary
from app.models.game.room import RoomError
from app.models.game.session import LocalSession
from app.models.database.session import make_session_factory
from app.models.services.room_service import RoomService
from tests.test_bot_flow import Env, run


def deleted(env):
    return [c.message_id for c in env.session.of(DeleteMessage)]


def sent_ids(env):
    return [i for i in env.sent_message_ids()]


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)

    def sent_message_ids():
        return list(range(1000, 1000 + len(e.session.of(SendMessage)) + 50))

    e.sent_message_ids = sent_message_ids
    return e


def test_new_section_replaces_previous_section_message(env):
    async def scenario():
        await env.text(1, "Наборы слов")
        first = env.tracker.get(1)
        await env.text(1, "История игр")
        second = env.tracker.get(1)
        await env.text(1, "Профиль")
        return first, second, env.tracker.get(1)

    first, second, third = run(scenario())
    assert first == second == third
    assert first not in deleted(env)
    assert len(env.session.of(SendMessage)) == 1
    assert len(env.session.of(EditMessageText)) == 2


def test_close_button_deletes_screen(env):
    async def scenario():
        await env.press(1, "menu:home", message_id=777)

    run(scenario())
    assert 777 in deleted(env)


def test_pack_flow_replaces_prompt_instead_of_piling_up(env):
    async def scenario():
        await env.press(1, "pk:new", message_id=500)
        await env.text(1, "Набор")
        after_title = env.tracker.get(1)
        await env.text(1, "кот, пёс")
        await env.text(1, "лиса")
        return after_title, env.tracker.get(1)

    after_title, last = run(scenario())
    assert after_title == last == 500
    assert 500 not in deleted(env)
    assert last not in deleted(env)


def test_room_panel_is_not_deleted_by_menu_sections_but_replaced_on_reopen(env):
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        room_id = env.service.room_id_of(1)
        await env.text(1, "Профиль")
        assert 700 not in deleted(env)
        await env.text(1, "Моя комната")
        panel_after = [p["panel"] for p in env.service.get(room_id).players if p["id"] == 1][0]
        return room_id, panel_after

    room_id, panel_after = run(scenario())
    assert 700 in deleted(env) and panel_after != 700


def test_reopening_room_twice_keeps_single_panel(env):
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        room_id = env.service.room_id_of(1)
        await env.text(1, "Моя комната")
        first = [p["panel"] for p in env.service.get(room_id).players][0]
        await env.text(1, "Моя комната")
        second = [p["panel"] for p in env.service.get(room_id).players][0]
        return first, second

    first, second = run(scenario())
    assert first != second and first in deleted(env) and second not in deleted(env)


def test_leave_room_requires_confirmation_and_deletes_panel_after_confirm(env):
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        await env.press(1, "rm:leave", message_id=700)
        assert env.service.room_id_of(1) is not None
        prompt_id = env.session.sent_ids[-1]
        confirmation = env.session.of(SendMessage)[-1].reply_markup.inline_keyboard[0][0].callback_data
        await env.press(1, confirmation, message_id=prompt_id)

    run(scenario())
    assert env.service.room_id_of(1) is None and 700 in deleted(env)


def test_local_game_screen_survives_menu_sections(env):
    async def scenario():
        await env.text(4, "Один телефон")
        game_msg = env.local_repo.load(4)["message_id"]
        await env.text(4, "Профиль")
        await env.text(4, "Наборы слов")
        return game_msg

    game_msg = run(scenario())
    assert game_msg not in deleted(env)


def test_local_exit_deletes_message(env):
    async def scenario():
        await env.text(4, "Один телефон")
        msg = env.local_repo.load(4)["message_id"]
        await env.press(4, "lg:0:menu", message_id=msg)
        return msg

    msg = run(scenario())
    assert msg in deleted(env) and env.local_repo.load(4) is None


def test_timezone_is_a_separate_settings_submenu(env):
    async def scenario():
        await env.text(5, "Настройки")
        settings_msg = env.session.of(SendMessage)[-1]
        await env.press(5, "set:tzmenu", message_id=env.tracker.get(5))
        timezone_msg = env.session.of(EditMessageText)[-1]
        await env.press(5, "set:tzset:3", message_id=env.tracker.get(5))
        return settings_msg, timezone_msg

    settings_msg, timezone_msg = run(scenario())
    settings_buttons = [b.callback_data for r in settings_msg.reply_markup.inline_keyboard for b in r]
    assert "set:tzmenu" in settings_buttons and "set:tzset:3" not in settings_buttons
    zone_buttons = [b.callback_data for r in timezone_msg.reply_markup.inline_keyboard for b in r]
    assert "set:tzset:3" in zone_buttons and "set:tzset:0" in zone_buttons
    assert env.user_settings.tz_minutes(5) == 180
    edit = env.session.of(EditMessageText)[-1]
    assert "Москва (UTC+3)" in edit.text
    assert any(b.text.startswith("✓") for r in edit.reply_markup.inline_keyboard for b in r)


def test_deeplink_without_join_falls_through_to_start_menu(env):
    run(env.text(6, "/start whatever"))
    last = env.session.of(SendMessage)[-1]
    assert last.text == __import__("app.views.telegram.keyboards.menu", fromlist=["MENU_TEXT"]).MENU_TEXT and last.reply_markup is not None


def make_service(tmp_path, library=None):
    factory = make_session_factory(f"sqlite:///{tmp_path}/ui.db")
    return RoomService(factory, library=library, rng=random.Random(1))


def test_room_settings_choose_values_directly(tmp_path):
    service = make_service(tmp_path)
    upd = service.create(1, "Хост")
    for uid in (2, 3):
        service.join_code(uid, f"И{uid}", upd.room.code)
    service.act(1, "ui:settings")
    service.act(1, "sv:rounds:7")
    service.act(1, "sv:turn:120")
    service.act(1, "sv:max:4")
    room = service.get(upd.room_id)
    assert room.settings["rounds"] == 7 and room.settings["turn_seconds"] == 120
    assert room.settings["max_players"] == 4
    labels = [label for row in room.view_for(1, "bot", 0).rows for label, _ in row]
    assert "[x] 7" in labels and "[x] 120" in labels and "[x] 4" in labels
    assert "Раундов в игре" in labels and "3" in labels and "10" in labels


def test_room_settings_validation_and_permissions(tmp_path):
    service = make_service(tmp_path)
    upd = service.create(1, "Хост")
    for uid in (2, 3, 4, 5):
        service.join_code(uid, f"И{uid}", upd.room.code)
    for action in ("sv:rounds:99", "sv:bogus:1", "sv:rounds:x", "sv:max:4"):
        with pytest.raises(RoomError):
            service.act(1, action)
    for action in ("sv:rounds:3", "tall", "tnone"):
        with pytest.raises(RoomError):
            service.act(2, action)
    labels = [label for row in service.get(upd.room_id).view_for(1, "bot", 0).rows for label, _ in row]
    assert upd.room_id and service.get(upd.room_id).settings["rounds"] == 5
    service.act(1, "ui:settings")
    labels = [label for row in service.get(upd.room_id).view_for(1, "bot", 0).rows for label, _ in row]
    assert "4" not in labels and "6" in labels


def test_test_lobby_has_no_player_count_row_and_rejects_change(tmp_path):
    service = make_service(tmp_path)
    upd = service.create(1, "Админ", test_size=2)
    service.act(1, "ui:settings")
    labels = [label for row in service.get(upd.room_id).view_for(1, "bot", 0).rows for label, _ in row]
    assert "Игроков в комнате" not in labels and "Раундов в игре" in labels
    with pytest.raises(RoomError):
        service.act(1, "sv:max:6")


def test_room_select_all_and_clear_themes_and_noop(tmp_path):
    service = make_service(tmp_path)
    upd = service.create(1, "Хост")
    service.act(1, "ui:themes")
    service.act(1, "tall")
    all_ids = [p.id for p in test_library().packs()]
    assert service.get(upd.room_id).settings["themes"] == all_ids
    service.act(1, "tnone")
    assert service.get(upd.room_id).settings["themes"] == []
    result = service.act(1, "noop")
    assert result.changed is False


def test_local_select_all_and_clear_themes():
    s = LocalSession(rng=random.Random(1), library=test_library())
    s.act(s.seq, "n:3")
    for name in ["А", "Б", "В"]:
        s.handle_text(name)
    s.act(s.seq, "th")
    actions = [a for row in s.screen().rows for _, a in row]
    assert "tall" in actions and "tnone" in actions
    s.act(s.seq, "tall")
    assert len(s.themes) == len(test_library().packs())
    s.act(s.seq, "tnone")
    assert s.themes == []


def test_tracker_basics():
    tracker = ScreenTracker()
    tracker.set(1, 10)
    tracker.discard(1, 99)
    assert tracker.get(1) == 10
    tracker.discard(1, 10)
    assert tracker.get(1) is None
    tracker.set(2, 5)
    assert tracker.pop(2) == 5 and tracker.pop(2) is None


class FakeEvent:
    def __init__(self, data, message_id):
        from aiogram.types import CallbackQuery

        self.__class__ = type("E", (CallbackQuery,), {})
        self._data, self._mid = data, message_id


def test_track_middleware_classification(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.press(1, "pk:menu", message_id=11)
        a = env.tracker.get(1)
        await env.press(1, "rm:leave", message_id=12)
        b = env.tracker.get(1)
        await env.press(1, "lg:0:menu", message_id=13)
        c = env.tracker.get(1)
        await env.press(1, "hist:menu", message_id=14)
        d = env.tracker.get(1)
        await env.press(1, "menu:local", message_id=14)
        e = env.tracker.get(1)
        return a, b, c, d, e

    a, b, c, d, e = run(scenario())
    assert a == 11 and b == 11 and c == 11 and d == 14 and e is None


def test_settings_toggles_edit_one_screen_without_sending_duplicate_menu(env):
    async def scenario():
        await env.text(12, "/start")
        welcome_id = env.tracker.get_anchor(12)
        await env.text(12, "Настройки")
        settings_id = env.tracker.get(12)
        sends_before = len(env.session.of(SendMessage))
        await env.press(12, "set:emoji:0", message_id=settings_id)
        await env.press(12, "set:colors:0", message_id=settings_id)
        return welcome_id, settings_id, sends_before, len(env.session.of(SendMessage))

    welcome_id, settings_id, sends_before, sends_after = run(scenario())
    assert welcome_id not in deleted(env)
    assert settings_id not in deleted(env)
    assert sends_after == sends_before
    assert env.tracker.get(12) == settings_id


def test_keyboard_anchor_survives_entering_local_game(env):
    async def scenario():
        await env.text(13, "/start")
        welcome_id = env.tracker.get_anchor(13)
        await env.text(13, "Один телефон")
        return welcome_id

    welcome_id = run(scenario())
    assert welcome_id not in deleted(env)
    assert env.tracker.get_anchor(13) == welcome_id
