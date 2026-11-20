from aiogram.methods import DeleteMessages, EditMessageText, SendMessage
from tests.test_bot_flow import env, run


def test_cancel_join_and_close_clear_state(env):
    async def scenario():
        await env.press(2, "online:join", message_id=500)
        await env.text(2, "bad")
        assert env.tracker.get(2) == 500
        assert not env.session.of(SendMessage)
        await env.press(2, "menu:online", message_id=500)
        context = env.dp.fsm.get_context(bot=env.bot, chat_id=2, user_id=2)
        assert await context.get_state() is None
        await env.press(2, "online:join", message_id=500)
        await env.press(2, "menu:home", message_id=500)
        assert await context.get_state() is None
        assert env.tracker.get(2) is None
    run(scenario())


def test_start_preserves_both_game_panels(env):
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        await env.text(1, "Один телефон")
        panel = env.local_repo.load(1)["message_id"]
        await env.text(1, "Профиль")
        await env.text(1, "/start")
        from aiogram.methods import DeleteMessage
        deleted = [m.message_id for m in env.session.of(DeleteMessage)]
        assert 700 not in deleted and panel not in deleted
        assert not env.session.of(DeleteMessages)
    run(scenario())


def test_game_panel_rejects_stale_navigation(env):
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        await env.press(1, "set:menu", message_id=700)
        await env.press(1, "menu:home", message_id=700)
        assert env.tracker.get(1) is None
        from aiogram.methods import DeleteMessage
        assert 700 not in [m.message_id for m in env.session.of(DeleteMessage)]
        assert not any(m.text.startswith("⚙") for m in env.session.of(EditMessageText))
    run(scenario())


def test_timezone_returns_to_settings_without_new_message(env):
    async def scenario():
        await env.text(1, "Настройки")
        mid = env.tracker.get(1)
        await env.press(1, "set:tzmenu", message_id=mid)
        await env.press(1, "set:tzset:3", message_id=mid)
        await env.press(1, "set:menu", message_id=mid)
        assert len(env.session.of(SendMessage)) == 1
        assert "Москва (UTC+3)" in env.session.of(EditMessageText)[-1].text
    run(scenario())


def test_cancel_word_edit_does_not_modify_word(env):
    async def scenario():
        pid = env.packs.create_pack(1, "Owner", "Test", ["кот"])
        words, _ = env.packs.list_words(1, pid, 0, 10)
        wid = words[0][0]
        await env.press(1, f"pk:wed:{wid}")
        await env.press(1, f"pk:w:{wid}")
        context = env.dp.fsm.get_context(bot=env.bot, chat_id=1, user_id=1)
        assert await context.get_state() is None
        await env.text(1, "пёс")
        assert env.packs.word(1, wid)[1] == "кот"
    run(scenario())


def test_close_old_screen_keeps_current_input(env):
    async def scenario():
        await env.press(1, "pk:new", message_id=600)
        await env.press(1, "menu:home", message_id=500)
        context = env.dp.fsm.get_context(bot=env.bot, chat_id=1, user_id=1)
        assert await context.get_state() == "PackStates:title"
        assert env.tracker.get(1) == 600
    run(scenario())


def test_settings_disabled_emoji_buttons_are_plain(env):
    from app.bot.emoji import ButtonStyleMiddleware
    env.bot.session.middleware(ButtonStyleMiddleware(env.user_settings.prefs))
    async def scenario():
        await env.text(1, "Настройки")
        mid = env.tracker.get(1)
        await env.press(1, "set:emoji:1", message_id=mid)
        await env.press(1, "set:emoji:0", message_id=env.tracker.get(1))
        labels = [b.text for row in env.session.of(SendMessage)[-1].reply_markup.inline_keyboard for b in row]
        assert labels[0].startswith("Эмодзи:")
        assert labels[1].startswith("Цветные кнопки:")
        assert len(env.session.of(SendMessage)) == 5
    run(scenario())
