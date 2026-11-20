from aiogram.methods import DeleteMessage, EditMessageText, SendMessage
from aiogram.types import ReplyKeyboardMarkup
import pytest

from app.bot.middlewares.navigation import KeepMenuMiddleware
from tests.test_bot_flow import Env, run


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    e.dp.message.outer_middleware(KeepMenuMiddleware())
    e.dp.callback_query.outer_middleware(KeepMenuMiddleware())
    return e


def deleted(env):
    return {m.message_id for m in env.session.of(DeleteMessage)}


@pytest.mark.parametrize("section", ["Настройки", "Наборы слов", "История игр", "Профиль", "Рейтинг", "Играть онлайн", "/rules"])
def test_all_sections_keep_keyboard_after_open_and_close(env, section):
    async def scenario():
        await env.text(1, "/start")
        carrier = env.tracker.get_anchor(1)
        await env.text(1, section)
        screen = env.tracker.get(1)
        assert carrier not in deleted(env)
        assert screen != carrier
        await env.press(1, "menu:home", message_id=screen)
        assert screen in deleted(env)
        assert carrier not in deleted(env)
        assert env.tracker.get_anchor(1) == carrier
        await env.text(1, "Настройки")
        assert env.tracker.get(1) is not None
        keyboard_messages = [m for m in env.session.of(SendMessage) if isinstance(m.reply_markup, ReplyKeyboardMarkup)]
        assert len(keyboard_messages) == 1
        assert keyboard_messages[0].text == "Меню"
    run(scenario())


def test_restart_recovers_keyboard_on_settings_callback(env):
    async def scenario():
        await env.press(1, "set:tzmenu", message_id=env.tracker.get(1) or 500)
        carrier = env.tracker.get_anchor(1)
        assert carrier is not None
        assert env.tracker.get(1) > carrier
        await env.press(1, "set:tzset:3", message_id=env.tracker.get(1) or 500)
        await env.press(1, "set:menu", message_id=env.tracker.get(1) or 500)
        await env.press(1, "set:emoji:1", message_id=env.tracker.get(1) or 500)
        await env.press(1, "set:colors:1", message_id=env.tracker.get(1))
        assert carrier in deleted(env)
        current = env.tracker.get_anchor(1)
        await env.press(1, "menu:home", message_id=env.tracker.get(1))
        assert current not in deleted(env)
    run(scenario())


def test_settings_command_without_start_restores_keyboard(env):
    async def scenario():
        await env.text(1, "/settings")
        carrier = env.tracker.get_anchor(1)
        screen = env.tracker.get(1)
        assert carrier and screen and carrier != screen
        await env.press(1, "menu:home", message_id=screen)
        assert carrier not in deleted(env)
    run(scenario())


def test_cancel_input_keeps_exactly_one_keyboard(env):
    async def scenario():
        await env.text(1, "/start")
        old = env.tracker.get_anchor(1)
        await env.press(1, "online:join", message_id=500)
        await env.text(1, "/cancel")
        current = env.tracker.get_anchor(1)
        assert old in deleted(env) and current not in deleted(env)
        context = env.dp.fsm.get_context(bot=env.bot, chat_id=1, user_id=1)
        assert await context.get_state() is None
        await env.text(1, "Настройки")
        assert current not in deleted(env)
    run(scenario())


def test_local_and_online_panels_are_separate_from_keyboard(env):
    async def scenario():
        await env.text(1, "/start")
        carrier = env.tracker.get_anchor(1)
        await env.text(1, "Один телефон")
        local = env.local_repo.load(1)["message_id"]
        await env.text(1, "Играть онлайн")
        online = env.tracker.get(1)
        await env.press(1, "online:create", message_id=online)
        await env.text(1, "Настройки")
        await env.press(1, "menu:home", message_id=env.tracker.get(1))
        assert not ({carrier, local, online} & deleted(env))
    run(scenario())


def test_first_deeplink_has_separate_permanent_keyboard(env):
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        room = env.service.get(env.service.room_id_of(1))
        await env.text(2, f"/start join_{room.token}")
        carrier = env.tracker.get_anchor(2)
        joined = env.service.get(room.room_id) if hasattr(room, "room_id") else env.service.get(env.service.room_id_of(2))
        panel = next(p["panel"] for p in joined.players if p["id"] == 2)
        assert carrier and panel and carrier != panel
        keyboards = [m for m in env.session.of(SendMessage) if m.chat_id == 2 and isinstance(m.reply_markup, ReplyKeyboardMarkup)]
        assert len(keyboards) == 1 and keyboards[0].text == "Меню"
        await env.text(2, "Настройки")
        await env.press(2, "menu:home", message_id=env.tracker.get(2))
        assert carrier not in deleted(env) and panel not in deleted(env)
    run(scenario())


def test_old_room_action_after_restart_restores_keyboard(env):
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        env.tracker.pop_anchor(1)  # in-memory tracker is empty after restart
        before = len(env.session.of(SendMessage))
        await env.press(1, "rm:noop", message_id=700)
        carrier = env.tracker.get_anchor(1)
        assert carrier is not None
        assert len(env.session.of(SendMessage)) == before + 1
        assert isinstance(env.session.of(SendMessage)[-1].reply_markup, ReplyKeyboardMarkup)
    run(scenario())


def test_style_switches_refresh_bottom_keyboard_immediately(env):
    from app.bot.emoji import ButtonStyleMiddleware
    env.bot.session.middleware(ButtonStyleMiddleware(env.user_settings.prefs))
    async def scenario():
        await env.text(1, "/start")
        await env.text(1, "Настройки")
        screen = env.tracker.get(1)
        for action, emoji, colors in (
            ("set:emoji:1", True, False),
            ("set:colors:1", True, True),
            ("set:emoji:0", False, True),
            ("set:colors:0", False, False),
        ):
            old = env.tracker.get_anchor(1)
            before = len(env.session.of(SendMessage))
            await env.press(1, action, message_id=screen)
            assert len(env.session.of(SendMessage)) == before + 2
            keyboard_message = env.session.of(SendMessage)[-2]
            button = keyboard_message.reply_markup.keyboard[0][0]
            assert button.text == ("🎮 Играть онлайн" if emoji else "Играть онлайн")
            assert button.style == ("success" if colors else None)
            assert keyboard_message.disable_notification is True
            assert old in deleted(env)
            assert env.tracker.get_anchor(1) not in deleted(env)
            assert screen in deleted(env)
            screen = env.tracker.get(1)
            assert screen > env.tracker.get_anchor(1)
            assert screen not in deleted(env)
            assert env.session.of(SendMessage)[-1].text.startswith("⚙️ Настройки")
            sent_keyboards = {mid for mid, m in zip(env.session.sent_ids, env.session.of(SendMessage)) if isinstance(m.reply_markup, ReplyKeyboardMarkup)}
            assert sent_keyboards - deleted(env) == {env.tracker.get_anchor(1)}
        before = len(env.session.of(SendMessage))
        await env.press(1, "set:colors:0", message_id=screen)
        assert len(env.session.of(SendMessage)) == before
        current = env.tracker.get_anchor(1)
        await env.press(1, "menu:home", message_id=screen)
        assert current not in deleted(env)
    run(scenario())


def test_style_refresh_keeps_admin_button(env):
    from app.bot.emoji import ButtonStyleMiddleware
    env.bot.session.middleware(ButtonStyleMiddleware(env.user_settings.prefs))
    async def scenario():
        await env.text(1, "/start", username="ExampleAdmin")
        await env.text(1, "Настройки", username="ExampleAdmin")
        screen = env.tracker.get(1)
        await env.press(1, "set:colors:1", username="ExampleAdmin", message_id=screen)
        labels = [b.text for row in env.session.of(SendMessage)[-2].reply_markup.keyboard for b in row]
        assert "Админ" in labels
        await env.text(2, "/start", username="guest")
        await env.text(2, "Настройки", username="guest")
        await env.press(2, "set:colors:1", username="guest", message_id=env.tracker.get(2))
        labels = [b.text for row in env.session.of(SendMessage)[-2].reply_markup.keyboard for b in row]
        assert "Админ" not in labels
    run(scenario())


def test_old_settings_callback_cannot_reclaim_new_card(env):
    async def scenario():
        await env.text(1, "/start")
        await env.text(1, "Настройки")
        previous = env.tracker.get(1)
        await env.press(1, "set:emoji:1", message_id=previous)
        current = env.tracker.get(1)
        assert current != previous
        before = len(env.session.of(SendMessage))
        await env.press(1, "set:emoji:0", message_id=previous)
        assert env.user_settings.prefs(1).emoji is True
        assert env.tracker.get(1) == current
        assert len(env.session.of(SendMessage)) == before
        await env.press(1, "menu:home", message_id=current)
        assert current in deleted(env)
        assert env.tracker.get_anchor(1) not in deleted(env)
    run(scenario())


def test_keyboard_carrier_id_is_saved_across_restart(tmp_path):
    from app.bot.tracker import ScreenTracker
    path = tmp_path / "anchors.json"
    tracker = ScreenTracker(path)
    tracker.set_anchor(1, 100)
    tracker.set_anchor(2, 200)
    restored = ScreenTracker(path)
    assert restored.get_anchor(1) == 100
    assert restored.get_anchor(2) == 200
    restored.pop_anchor(1)
    again = ScreenTracker(path)
    assert again.get_anchor(1) is None and again.get_anchor(2) == 200
    assert not path.with_suffix(".tmp").exists()
