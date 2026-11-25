import asyncio
import logging

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage

from app.controllers.telegram.errors import HarmlessBadRequest, on_harmless_bad_request, on_network_error
from app.views.telegram.ui import safe_answer
from app.core.config import load_settings
from tests.test_bot_flow import Env, run


class FailingCall:
    def __init__(self, error):
        self.error = error
        self.calls = 0

    async def answer(self, *args, **kwargs):
        self.calls += 1
        raise self.error


def network_error(method=None):
    return TelegramNetworkError(method=method or AnswerCallbackQuery(callback_query_id="1"), message="timeout")


def stale_error():
    return TelegramBadRequest(
        method=AnswerCallbackQuery(callback_query_id="1"),
        message="Bad Request: query is too old and response timeout expired or query ID is invalid",
    )


def test_safe_answer_swallows_network_and_stale_errors(caplog):
    for error in (network_error(), stale_error()):
        call = FailingCall(error)
        with caplog.at_level(logging.WARNING):
            asyncio.run(safe_answer(call, "text", show_alert=True))
        assert call.calls == 1
    assert "Не удалось ответить" in caplog.text


def make_room(env):
    service = env.service
    upd = service.create(1, "Хост")
    service.join_code(2, "Гость", upd.room.code)
    return upd.room_id


def test_room_panels_refresh_even_when_callback_answer_fails(tmp_path):
    env = Env(tmp_path)
    rid = make_room(env)
    env.service.set_panel(rid, 2, 900)
    env.session.fail_on[AnswerCallbackQuery] = network_error

    async def scenario():
        await env.press(2, "rm:rd:1", message_id=900)

    run(scenario())
    room = env.service.get(rid)
    assert [p["ready"] for p in room.players if p["id"] == 2] == [True]
    edited_chats = {c.chat_id for c in env.session.of(EditMessageText)} | {
        c.chat_id for c in env.session.of(SendMessage)
    }
    assert 1 in edited_chats


def test_network_failure_inside_handler_does_not_crash_dispatcher(tmp_path):
    env = Env(tmp_path)
    env.session.fail_on[SendMessage] = network_error

    async def scenario():
        await env.text(5, "Профиль")
        await env.text(5, "Рейтинг")

    run(scenario())
    assert len(env.session.of(SendMessage)) >= 2


def test_stale_callback_error_is_treated_as_harmless():
    class Event:
        exception = stale_error()

    assert asyncio.run(HarmlessBadRequest()(Event())) is True
    assert asyncio.run(on_harmless_bad_request(Event())) is True
    assert asyncio.run(on_network_error(type("E", (), {"exception": network_error()})())) is True

    class Other:
        exception = TelegramBadRequest(
            method=AnswerCallbackQuery(callback_query_id="1"), message="Bad Request: chat not found"
        )

    assert asyncio.run(HarmlessBadRequest()(Other())) is False


def test_runtime_does_not_duplicate_panel_on_network_error(tmp_path):
    env = Env(tmp_path)
    rid = make_room(env)

    async def scenario():
        await env.runtime.refresh(rid)
        panels = {p["id"]: p["panel"] for p in env.service.get(rid).players}
        sends_before = len(env.session.of(SendMessage))
        env.session.fail_on[EditMessageText] = network_error
        await env.runtime.refresh(rid)
        return panels, sends_before

    panels, sends_before = run(scenario())
    assert all(panels.values())
    assert len(env.session.of(SendMessage)) == sends_before
    assert {p["id"]: p["panel"] for p in env.service.get(rid).players} == panels


def test_proxy_url_is_read_from_environment(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1:abc")
    monkeypatch.setenv("PROXY_URL", " http://127.0.0.1:8080 ")
    assert load_settings().proxy_url == "http://127.0.0.1:8080"
    monkeypatch.setenv("PROXY_URL", "")
    assert load_settings().proxy_url is None
