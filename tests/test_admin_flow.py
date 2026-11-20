from app.bot.emoji import strip_decor
import pytest
from aiogram.methods import DeleteMessage, DeleteMessages, EditMessageText, SendMessage

from app.core.config import Settings, load_settings
from tests.test_bot_flow import Env, run

ADMIN_ID = 700000001


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    e.dp["settings"] = Settings(
        bot_token="x", database_url="x", admin_username="ExampleAdmin", admin_id=ADMIN_ID
    )
    return e


def labels(message):
    return [b.text for row in message.reply_markup.keyboard for b in row]


def test_admin_by_id_even_if_username_differs(env):
    async def scenario():
        await env.text(ADMIN_ID, "/start", username="completely_other")
        await env.text(5, "/start", username="ExampleAdmin")

    run(scenario())
    first, second = env.session.of(SendMessage)[:2]
    assert "Админ" in labels(first)
    assert "Админ" not in labels(second)


def test_admin_menu_for_admin_and_denied_for_others(env):
    async def scenario():
        await env.text(ADMIN_ID, "Админ", username="x")
        await env.text(5, "Админ", username="ExampleAdmin")
        await env.text(5, "/admin", username="ExampleAdmin")
        await env.text(ADMIN_ID, "/admin", username="x")

    run(scenario())
    sends = env.session.of(SendMessage)
    assert not any(c.chat_id == 5 for c in sends)
    admin_sends = [c for c in sends if c.chat_id == ADMIN_ID]
    assert len(admin_sends) == 1
    assert len(env.session.of(EditMessageText)) == 1
    assert all(c.text.startswith("Администрирование") for c in admin_sends)
    admin_message = [c for c in env.session.of(SendMessage) if c.text.startswith("Администрирование")][0]
    data = [b.callback_data for r in admin_message.reply_markup.inline_keyboard for b in r]
    assert {"online:test:1", "online:test:2", "pk:mod:0"} <= set(data)


def test_whoami_shows_id_username_and_admin_flag(env):
    async def scenario():
        await env.text(ADMIN_ID, "/whoami", username="boss")
        await env.text(7, "/whoami", username="guest")

    run(scenario())
    first, second = env.session.of(SendMessage)[:2]
    assert f"ID: {ADMIN_ID}" in first.text and "Администратор: да" in first.text
    assert "Логин: @boss" in first.text
    assert "Администратор: нет" in second.text


def test_admin_can_create_test_lobby_via_buttons(env):
    async def scenario():
        await env.press(ADMIN_ID, "online:test:2", username="x")

    run(scenario())
    room = env.service.get(env.service.room_id_of(ADMIN_ID))
    assert room.is_test and room.settings["test_size"] == 2


def test_start_wipes_old_messages_but_keeps_game_panels(env):
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        await env.text(4, "Один телефон")
        local_msg = env.local_repo.load(4)["message_id"]
        await env.text(1, "/start")
        await env.text(4, "/start")
        return local_msg

    local_msg = run(scenario())
    assert not env.session.of(DeleteMessages)
    deleted = [c.message_id for c in env.session.of(DeleteMessage)]
    assert 700 not in deleted and local_msg not in deleted


def test_rules_and_settings_commands(env):
    async def scenario():
        await env.text(5, "/rules")
        await env.text(5, "/settings")

    run(scenario())
    texts = [strip_decor(c.text) for c in env.session.of((SendMessage, EditMessageText))]
    assert any(t.startswith("Правила") for t in texts)
    assert any(t.startswith("Настройки") for t in texts)


def test_main_menu_message_has_no_inline_duplicate_menu(env):
    run(env.text(5, "/start"))
    message = env.session.of(SendMessage)[0]
    assert message.reply_markup.__class__.__name__ == "ReplyKeyboardMarkup"


def test_settings_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:abc")
    monkeypatch.setenv("ADMIN_USERNAME", " ExampleAdmin ")
    monkeypatch.setenv("ADMIN_ID", "700000001")
    settings = load_settings()
    assert settings.admin_username == "ExampleAdmin" and settings.admin_id == ADMIN_ID
    monkeypatch.setenv("ADMIN_ID", "")
    assert load_settings().admin_id is None
    monkeypatch.setenv("ADMIN_ID", "abc")
    assert load_settings().admin_id is None
