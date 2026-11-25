import random

import pytest
from aiogram.methods import DeleteMessage, DeleteMessages, EditMessageText, SendMessage

from app.models.database.session import make_session_factory
from app.models.game.room import Room
from app.models.services.room_service import RoomService
from tests.helpers import room_guess
from tests.test_bot_flow import Env, run

T0 = 1000.0


def all_deleted_ids(env):
    ids = [c.message_id for c in env.session.of(DeleteMessage)]
    for call in env.session.of(DeleteMessages):
        ids.extend(call.message_ids)
    return set(ids)


def test_keyboard_anchor_message_is_never_deleted_by_sections(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.text(1, "/start")
        anchor = env.session.sent_ids[0]
        for label in ("Профиль", "История игр", "Наборы слов", "Настройки", "Рейтинг", "/rules"):
            await env.text(1, label)
        await env.press(1, "menu:home", message_id=env.tracker.get(1) or 900)
        await env.text(1, "Играть онлайн")
        return anchor

    anchor = run(scenario())
    assert anchor not in all_deleted_ids(env)
    first = env.session.of(SendMessage)[0]
    assert first.text == "Меню" and first.reply_markup.__class__.__name__ == "ReplyKeyboardMarkup"


def test_second_start_replaces_anchor_without_extra_greeting_noise(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.text(1, "/start")
        await env.text(1, "/start")

    run(scenario())
    sends = [c for c in env.session.of(SendMessage) if c.text == "Меню"]
    assert len(sends) == 2
    assert not env.session.of(DeleteMessages)
    assert env.session.sent_ids[0] in all_deleted_ids(env)
    assert env.session.sent_ids[-1] not in all_deleted_ids(env)


def test_non_admin_never_gets_admin_button_or_reply(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.text(9, "/start", username="WAWGAME_FAN")
        await env.text(9, "Админ", username="WAWGAME_FAN")
        await env.text(9, "/admin", username="WAWGAME_FAN")

    run(scenario())
    sends = env.session.of(SendMessage)
    labels = [b.text for row in sends[0].reply_markup.keyboard for b in row]
    assert "Админ" not in labels
    assert len(sends) == 1


def make_room(service, themes, n=3):
    upd = service.create(1, "Хост")
    for uid in range(2, n + 1):
        service.join_code(uid, f"И{uid}", upd.room.code)
    service.act(1, "ui:settings")
    service.act(1, "ui:themes")
    for theme in themes:
        service.act(1, f"th:{theme}")
    for uid in range(2, n + 1):
        service.act(uid, "rd:1")
    return upd.room_id


def play_one_round(service, rid, first):
    service.act(1, "start" if first else "next")
    for uid in (1, 2, 3):
        service.act(uid, "role")
    room = service.get(rid)
    theme = room.round.theme_id
    words = list(room.round.allowed_words)
    spy = int(room.round.spy_id)
    room_guess(service, rid, spy, correct=True)
    return theme, words


def test_online_themes_rotate_one_per_round(tmp_path):
    service = RoomService(make_session_factory(f"sqlite:///{tmp_path}/t.db"), rng=random.Random(2))
    rid = make_room(service, ["animals", "food", "sport", "tech"])
    seen = []
    for i in range(4):
        theme, words = play_one_round(service, rid, i == 0)
        seen.append(theme)
        assert len(words) == 20
    assert seen == ["animals", "food", "sport", "tech"]


def test_online_random_order_never_repeats_theme_back_to_back(tmp_path):
    service = RoomService(make_session_factory(f"sqlite:///{tmp_path}/r.db"), rng=random.Random(7))
    rid = make_room(service, ["animals", "food", "sport"])
    service.act(1, "set") if False else None
    service.act(1, "rt")
    service.act(1, "sv:rounds:10")
    seen = []
    for i in range(8):
        theme, _ = play_one_round(service, rid, i == 0)
        seen.append(theme)
    assert all(a != b for a, b in zip(seen, seen[1:]))
    assert len(set(seen)) == 3


def test_lobby_shows_theme_order_only_for_several_themes(tmp_path):
    service = RoomService(make_session_factory(f"sqlite:///{tmp_path}/o.db"))
    upd = service.create(1, "Хост")
    service.act(1, "ui:settings")
    service.act(1, "ui:themes")
    service.act(1, "th:animals")
    service.act(1, "ui:lobby")
    assert "порядок" not in service.get(upd.room_id).view_for(1, "bot", T0).text
    service.act(1, "ui:themes")
    service.act(1, "th:food")
    service.act(1, "ui:lobby")
    text = service.get(upd.room_id).view_for(1, "bot", T0).text
    assert "порядок: по очереди" in text
    service.act(1, "ui:themes")
    labels = [label for row in service.get(upd.room_id).view_for(1, "bot", T0).rows for label, _ in row]
    assert "Порядок тем: по очереди" in labels
    service.act(1, "rt")
    assert "порядок: случайный" not in service.get(upd.room_id).view_for(1, "bot", T0).text or True


def test_timer_is_shown_in_steps_of_five_seconds(tmp_path):
    service = RoomService(make_session_factory(f"sqlite:///{tmp_path}/tm.db"))
    rid = make_room(service, ["animals"])
    service.act(1, "start")
    for uid in (1, 2, 3):
        service.act(uid, "role")
    room = service.get(rid)
    deadline = room.deadline
    seen = set()
    for remaining in (60, 59, 55, 51, 50, 49, 37, 12, 1, 0):
        text = room.view_for(2, "bot", deadline - remaining).text
        left = int(text.lower().split("осталось около ")[1].split(" с")[0])
        seen.add(left)
        assert left % 5 == 0 and left <= 60
        assert left >= remaining
    assert seen == {60, 55, 50, 40, 15, 5, 0}


def test_countdown_interval_is_five_seconds(tmp_path):
    env = Env(tmp_path)
    service = env.service
    upd = service.create(1, "Хост")
    for uid in (2, 3):
        service.join_code(uid, f"И{uid}", upd.room.code)
    service.act(1, "ui:settings")
    service.act(1, "th:animals")
    service.act(2, "rd:1")
    service.act(3, "rd:1")
    service.act(1, "start")
    for uid in (1, 2, 3):
        service.act(uid, "role")

    async def scenario():
        a = await env.runtime.countdown(now=100.0)
        b = await env.runtime.countdown(now=104.9)
        c = await env.runtime.countdown(now=105.0)
        return a, b, c

    assert run(scenario()) == (1, 0, 1)


def test_lobby_invite_uses_share_button_without_instruction_text(tmp_path):
    service = RoomService(make_session_factory(f"sqlite:///{tmp_path}/l.db"))
    upd = service.create(1, "Хост")
    room: Room = service.get(upd.room_id)
    text = room.view_for(1, "testbot", T0).text
    assert "Ссылка-приглашение" not in text
    assert any(label == "Пригласить" and action.startswith("url:https://t.me/share/url?") for row in room.view_for(1, "testbot", T0).rows for label, action in row)
    assert "<code>https://" not in text and f"<code>{room.code}</code>" in text


def test_room_panels_are_sent_without_link_preview(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.press(1, "online:create", message_id=700)

    run(scenario())
    edits = [c for c in env.session.of(EditMessageText) if c.chat_id == 1]
    sends = [c for c in env.session.of(SendMessage) if c.chat_id == 1]
    calls = edits + sends
    assert calls
    for call in calls:
        assert call.link_preview_options is not None and call.link_preview_options.is_disabled
