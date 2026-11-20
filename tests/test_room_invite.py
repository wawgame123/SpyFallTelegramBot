from urllib.parse import parse_qs, urlparse
from app.bot.runtime import to_room_markup
from tests.test_bot_flow import env, run


def test_invite_opens_share_with_exact_room_link_and_prepared_text(env):
    room = env.service.create(1, "Саша & Маша + 💬").room
    screen = room.view_for(1, "testbot", 0)
    assert "Ссылка-приглашение" not in screen.text
    button = next(b for row in to_room_markup(screen).inline_keyboard for b in row if b.text == "Пригласить")
    assert button.callback_data is None and button.copy_text is None
    parsed = urlparse(button.url)
    assert parsed.netloc == "t.me" and parsed.path == "/share/url"
    query = parse_qs(parsed.query)
    assert query["url"] == [f"https://t.me/testbot?start=join_{room.token}"]
    assert room.code in query["text"][0]
    assert room.title in query["text"][0]
    assert "Приглашаю сыграть" in query["text"][0]


def test_guest_can_also_invite_and_game_buttons_keep_working(env):
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        room = env.service.get(env.service.room_id_of(1))
        env.service.join_code(2, "Друг", room.code)
        room = env.service.get(env.service.room_id_of(1))
        keyboard = to_room_markup(room.view_for(2, "testbot", 0))
        assert any(b.url and b.url.startswith("https://t.me/share/url?") for row in keyboard.inline_keyboard for b in row)
        assert any(b.callback_data == "rm:rd:1" for row in keyboard.inline_keyboard for b in row)
        assert any(b.callback_data == "rm:leave" for row in keyboard.inline_keyboard for b in row)
    run(scenario())


def test_no_broken_invite_without_bot_username(env):
    room = env.service.create(1, "Хост").room
    keyboard = to_room_markup(room.view_for(1, "", 0))
    assert not any(b.url for row in keyboard.inline_keyboard for b in row)


def test_direct_link_is_under_code_and_copy_keeps_the_same_invite(env):
    room = env.service.create(1, "Хост").room
    screen = room.view_for(1, "testbot", 0)
    link = f"https://t.me/testbot?start=join_{room.token}"
    assert f'Код комнаты: <code>{room.code}</code>\n<a href="{link}">Ссылка</a>' in screen.text
    keyboard = to_room_markup(screen)
    copy = next(b for row in keyboard.inline_keyboard for b in row if b.text == "Скопировать ссылку")
    invite = next(b for row in keyboard.inline_keyboard for b in row if b.text == "Пригласить")
    assert copy.copy_text.text == link
    assert copy.url is None and copy.callback_data is None
    assert parse_qs(urlparse(invite.url).query)["url"] == [link]
    env.service.join_token(2, "Друг", urlparse(link).query.removeprefix("start=join_"))
    assert env.service.room_id_of(2) == env.service.room_id_of(1)


def test_closed_lobby_link_explains_inactivity_and_allows_new_room(env):
    async def scenario():
        update = env.service.create(1, "Хост")
        token = update.room.token
        env.service.act(1, "leave_confirm")
        await env.text(2, f"/start join_{token}")
        assert "Лобби неактивно" in env.last_text()
        assert env.service.room_id_of(2) is None
        from aiogram.methods import SendMessage
        markup = env.session.of(SendMessage)[-1].reply_markup
        buttons = [b.callback_data for row in markup.inline_keyboard for b in row]
        assert "online:create" in buttons and "online:join" in buttons and "menu:home" in buttons
        await env.press(2, "online:create", message_id=env.tracker.get(2))
        assert env.service.room_id_of(2) is not None
    run(scenario())


def test_expired_lobby_link_is_not_active(env):
    import pytest
    from app.game.room import RoomError
    update = env.service.create(1, "Хост")
    assert env.service.purge_stale(hours=-1) == 1
    with pytest.raises(RoomError, match="неактивно"):
        env.service.join_token(2, "Друг", update.room.token)
    assert env.service.room_id_of(2) is None


def test_finished_lobby_link_says_finished_instead_of_game_in_progress(env):
    from tests.test_history import new_room, play_round
    rid = new_room(env.service, n=3, host=10)
    room = play_round(env.service, rid, host=10)
    env.service.act(10, "end")
    async def scenario():
        await env.text(20, f"/start join_{room.token}")
        assert "уже завершено" in env.last_text()
        assert "Игра уже идёт" not in env.last_text()
        assert env.service.room_id_of(20) is None
    run(scenario())


def test_started_game_link_is_distinguished_from_closed_lobby(env):
    from tests.test_history import new_room
    rid = new_room(env.service, n=3, host=10)
    env.service.act(10, "start")
    room = env.service.get(rid)
    async def scenario():
        await env.text(20, f"/start join_{room.token}")
        assert "Игра уже идёт" in env.last_text()
        assert env.service.room_id_of(20) is None
    run(scenario())


def test_bad_invite_does_not_delete_existing_room_panel(env):
    async def scenario():
        from aiogram.methods import DeleteMessage, SendMessage
        await env.press(2, "online:create", message_id=800)
        own_room = env.service.room_id_of(2)
        await env.text(2, "/start join_invalid-token")
        assert "неактивно" in env.last_text()
        assert env.service.room_id_of(2) == own_room
        assert 800 not in {m.message_id for m in env.session.of(DeleteMessage)}
        buttons = [b.callback_data for row in env.session.of(SendMessage)[-1].reply_markup.inline_keyboard for b in row]
        assert "online:resume" in buttons
    run(scenario())
