import random
import re

import pytest

from app.bot.keyboards.menu import admin_lobby_menu, local_choice_menu, online_menu
from app.bot.keyboards.reply import ALL_BUTTONS, main_reply_keyboard
from app.core.config import Settings
from app.core.console import parse_command
from app.core.security import is_admin
from app.database.models import GameSession
from app.database.session import make_session_factory
from app.game.engine import Phase
from app.game.room import RoomError
from app.services.history_service import HistoryService
from app.services.room_service import RoomService
from tests.helpers import room_circle, room_guess

EMOJI = re.compile(
    "[\U0001F000-\U0001FFFF\u2190-\u21FF\u2300-\u23FF\u25A0-\u25FF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]"
)


@pytest.fixture
def service(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path}/t.db")
    return RoomService(factory, rng=random.Random(4)), factory


def test_username_without_configured_id_never_grants_admin_rights():
    settings = Settings(bot_token="x", database_url="x")
    for username in ("ExampleAdmin", "exampleadmin", "someone", None, ""):
        assert not is_admin(settings, 1, username)


def test_is_admin_id_is_authoritative_when_configured():
    settings = Settings(bot_token="x", database_url="x", admin_id=777)
    assert is_admin(settings, 777, "ExampleAdmin")
    assert is_admin(settings, 777, "another_name")
    assert is_admin(settings, 777, None)
    assert not is_admin(settings, 5, "ExampleAdmin")


def test_test_lobby_size_validation(service):
    svc, _ = service
    with pytest.raises(RoomError):
        svc.create(1, "А", test_size=3)
    with pytest.raises(RoomError):
        svc.create(1, "А", test_size=0)


def test_regular_lobby_still_needs_three_players(service):
    svc, _ = service
    upd = svc.create(1, "А")
    svc.join_code(2, "Б", upd.room.code)
    svc.act(1, "ui:settings")
    svc.act(1, "th:animals")
    svc.act(2, "rd:1")
    with pytest.raises(RoomError):
        svc.act(1, "start")
    assert not svc.get(upd.room_id).is_test


def test_solo_test_lobby_starts_and_player_is_spy(service):
    svc, _ = service
    upd = svc.create(1, "Админ", test_size=1)
    assert upd.room.is_test and "Тестовое" in upd.room.title
    with pytest.raises(RoomError):
        svc.join_code(2, "Лишний", upd.room.code)
    svc.act(1, "ui:settings")
    svc.act(1, "th:animals")
    svc.act(1, "start")
    room = svc.get(upd.room_id)
    assert room.round.spy_id == "1" and len(room.players) == 1
    alert = svc.act(1, "role").alert
    assert "шпион" in alert and room.round.word.upper() not in alert
    assert svc.get(upd.room_id).phase == "playing"


def test_solo_test_lobby_civilian_role_rejected(service):
    svc, _ = service
    upd = svc.create(1, "Админ", test_size=1)
    svc.act(1, "ui:settings")
    svc.act(1, "th:animals")
    svc.act(1, "cy:role")
    svc.act(1, "cy:role")
    assert svc.get(upd.room_id).settings["test_role"] == "civilian"
    with pytest.raises(RoomError):
        svc.act(1, "start")


def test_two_player_test_lobby_requires_both_and_role_choice(service):
    svc, _ = service
    upd = svc.create(1, "Админ", test_size=2)
    svc.act(1, "ui:settings")
    svc.act(1, "th:animals")
    with pytest.raises(RoomError):
        svc.act(1, "start")
    svc.join_code(2, "Друг", upd.room.code)
    with pytest.raises(RoomError):
        svc.join_code(3, "Третий", upd.room.code)
    svc.act(2, "rd:1")
    for wanted, expect_spy in (("spy", True), ("civilian", False)):
        room = svc.get(upd.room_id)
        while room.settings["test_role"] != wanted:
            svc.act(1, "cy:role")
            room = svc.get(upd.room_id)
        if room.phase == "result":
            svc.act(1, "next")
        else:
            svc.act(1, "start")
        room = svc.get(upd.room_id)
        assert (room.round.spy_id == "1") is expect_spy
        for uid in (1, 2):
            svc.act(uid, "role")
        room = svc.get(upd.room_id)
        spy = int(room.round.spy_id)
        room_guess(svc, upd.room_id, spy, correct=True)
        assert svc.get(upd.room_id).phase == "result"


def test_forced_role_holds_over_many_rounds(service):
    svc, _ = service
    upd = svc.create(1, "Админ", test_size=2)
    svc.join_code(2, "Друг", upd.room.code)
    svc.act(1, "ui:settings")
    svc.act(1, "th:animals")
    svc.act(1, "cy:rounds")
    svc.act(2, "rd:1")
    svc.act(1, "cy:role")
    assert svc.get(upd.room_id).settings["test_role"] == "spy"
    svc.act(1, "start")
    room = svc.get(upd.room_id)
    assert room.round.spy_id == "1"


def test_role_choice_and_max_change_only_in_test_lobby_and_by_host(service):
    svc, _ = service
    normal = svc.create(10, "Обычный")
    with pytest.raises(RoomError):
        svc.act(10, "cy:role")
    test = svc.create(1, "Админ", test_size=2)
    svc.join_code(2, "Друг", test.room.code)
    with pytest.raises(RoomError):
        svc.act(2, "cy:role")
    with pytest.raises(RoomError):
        svc.act(1, "cy:max")
    assert normal.room_id != test.room_id


def test_test_lobby_rounds_are_in_history_but_not_in_rating(service):
    svc, factory = service
    upd = svc.create(1, "Админ", test_size=1)
    svc.act(1, "ui:settings")
    svc.act(1, "th:animals")
    svc.act(1, "start")
    svc.act(1, "role")
    room_guess(svc, upd.room_id, 1, correct=True)
    assert svc.get(upd.room_id).phase == "result"
    history = HistoryService(factory)
    rounds, total = history.user_rounds(1, 0, 5)
    assert total == 1 and rounds[0].lobby_title == "Тестовое лобби на 1"
    lobbies, lobby_total = history.user_lobbies(1, 0, 5)
    assert lobby_total == 1 and lobbies[0].rounds_count == 1
    with factory() as db:
        assert db.query(GameSession).one().is_test is True
    from app.services.stats_service import StatsService

    stats = StatsService(factory)
    assert stats.profile(1) is None
    assert stats.leaderboard("points") == []


def test_lobby_view_shows_role_button_only_to_test_host(service):
    svc, _ = service
    upd = svc.create(1, "Админ", test_size=2)
    svc.join_code(2, "Друг", upd.room.code)
    room = svc.get(upd.room_id)
    host_actions = [a for row in room.view_for(1, "bot", 0).rows for _, a in row]
    guest_actions = [a for row in room.view_for(2, "bot", 0).rows for _, a in row]
    assert "cy:role" in host_actions and "cy:role" not in guest_actions
    normal = svc.create(10, "Обычный").room
    assert "cy:role" not in [a for row in normal.view_for(10, "bot", 0).rows for _, a in row]


def test_reply_keyboard_layout_and_admin_row_and_no_emoji():
    plain = main_reply_keyboard(False)
    admin = main_reply_keyboard(True)
    assert plain.is_persistent and plain.resize_keyboard
    labels = [b.text for row in plain.keyboard for b in row]
    admin_labels = [b.text for row in admin.keyboard for b in row]
    assert set(labels) < set(admin_labels) and "Админ" not in labels
    assert set(admin_labels) == ALL_BUTTONS
    assert len(plain.keyboard) == 4 and len(admin.keyboard) == 4
    assert all(len(row) <= 3 for row in admin.keyboard)
    for label in admin_labels:
        assert not EMOJI.search(label)


def test_inline_menus_admin_visibility():
    def texts(markup):
        return [b.text for row in markup.inline_keyboard for b in row]

    assert "Тестовые лобби" not in texts(online_menu(False, False))
    assert "Тестовые лобби" in texts(online_menu(False, True))
    assert "Тестовые лобби" not in texts(online_menu(True, True))
    assert len(admin_lobby_menu().inline_keyboard) == 4
    assert len(local_choice_menu().inline_keyboard) == 3


def test_console_command_parsing():
    assert parse_command("inv\n") == "hide"
    assert parse_command("  INV  ") == "hide"
    assert parse_command("hide") is None
    assert parse_command("") is None


def test_round_phase_in_one_player_test_lobby_votes_dont_deadlock_guess(service):
    svc, _ = service
    upd = svc.create(1, "Админ", test_size=1)
    svc.act(1, "ui:settings")
    svc.act(1, "th:animals")
    svc.act(1, "start")
    svc.act(1, "role")
    room_circle(svc, upd.room_id, True)
    room = svc.get(upd.room_id)
    assert room.round.phase == Phase.SUSPECT_VOTE
    room_guess(svc, upd.room_id, 1, correct=True)
    assert svc.get(upd.room_id).phase == "result"
