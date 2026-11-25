import asyncio
import gc
from datetime import datetime
from dataclasses import replace
from types import SimpleNamespace
import pytest
from sqlalchemy import event
from aiogram.methods import DeleteMessage, SendMessage, AnswerCallbackQuery
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.controllers.telegram.confirmations import LeaveConfirmations
from app.controllers.telegram.middlewares.private import PrivateCallbacksMiddleware
from app.controllers.telegram.middlewares import throttle
from app.controllers.telegram.middlewares.throttle import ThrottleMiddleware
from app.core.security import is_admin
from app.models.database.models import UserPenalty
from app.models.game.room import RoomError
from app.models.services.limits import FailedJoinLimiter
from tests.test_bot_flow import env, run


def prompt(env):
    sent = env.session.of(SendMessage)[-1]
    return env.session.sent_ids[-1], sent.reply_markup.inline_keyboard[0][0].callback_data


def deleted(env):
    return {m.message_id for m in env.session.of(DeleteMessage)}


def test_old_panel_cannot_modify_new_room(env):
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        env.service.act(1, "leave_confirm")
        await env.press(1, "online:create", message_id=800)
        current = env.service.room_id_of(1)
        sends = len(env.session.of(SendMessage))
        await env.press(1, "rm:rd:0", message_id=700)
        await env.press(1, "rm:leave", message_id=700)
        assert env.service.room_id_of(1) == current
        assert env.service.get(current).players[0]["ready"] is True
        assert len(env.session.of(SendMessage)) == sends
        assert 800 not in deleted(env)
        await env.press(1, "rm:rd:0", message_id=800)
        assert env.service.get(current).players[0]["ready"] is False
    run(scenario())


def test_panel_replaced_in_same_room_rejects_old_buttons(env):
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        await env.text(1, "Моя комната")
        current = env.service.panel_id_of(1)
        await env.press(1, "rm:rd:0", message_id=700)
        assert env.service.get(env.service.room_id_of(1)).players[0]["ready"]
        await env.press(1, "rm:rd:0", message_id=current)
        assert not env.service.get(env.service.room_id_of(1)).players[0]["ready"]
    run(scenario())


def test_guest_cannot_use_host_panel_or_host_actions(env):
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        rid = env.service.room_id_of(1)
        env.service.join_code(2, "Guest", env.service.get(rid).code)
        env.service.set_panel(rid, 2, 702)
        original = env.service.get(rid).settings["rounds"]
        await env.press(2, "rm:sv:rounds:7", message_id=700)
        await env.press(2, "rm:sv:rounds:7", message_id=702)
        assert env.service.get(rid).settings["rounds"] == original
    run(scenario())


def test_confirmation_from_previous_room_cannot_leave_current_room(env):
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        await env.press(1, "rm:leave", message_id=700)
        mid, confirm = prompt(env)
        env.service.act(1, "leave_confirm")
        await env.press(1, "online:create", message_id=800)
        current = env.service.room_id_of(1)
        await env.press(1, confirm, message_id=mid)
        assert env.service.room_id_of(1) == current
        assert 800 not in deleted(env)
        assert mid in deleted(env)
    run(scenario())


def test_valid_confirmation_is_single_use_and_duplicate_prompts_are_not_sent(env):
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        await env.press(1, "rm:leave", message_id=700)
        mid, confirm = prompt(env)
        sends = len(env.session.of(SendMessage))
        await env.press(1, "rm:leave", message_id=700)
        assert len(env.session.of(SendMessage)) == sends
        await env.press(1, confirm, message_id=mid)
        assert env.service.room_id_of(1) is None
        await env.press(1, "online:create", message_id=800)
        current = env.service.room_id_of(1)
        await env.press(1, confirm, message_id=mid)
        assert env.service.room_id_of(1) == current
    run(scenario())


def test_expired_legacy_and_tampered_confirmations_are_rejected(env):
    now = [100.0]
    env.runtime.leave_confirmations.clock = lambda: now[0]
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        await env.press(1, "rm:leave", message_id=700)
        mid, confirm = prompt(env)
        for invalid in ("rm_leave:confirm", "rm_leave:confirm:wrong", "rm_leave:other:wrong", "rm:leave_confirm"):
            await env.press(1, invalid, message_id=mid if invalid.startswith("rm_leave:") else 700)
            assert env.service.room_id_of(1) is not None
        await env.press(2, confirm, message_id=mid)
        assert env.service.room_id_of(1) is not None
        now[0] = 221
        await env.press(1, confirm, message_id=mid)
        assert env.service.room_id_of(1) is not None
        await env.press(1, "rm:leave", message_id=700)
        mid2, confirm2 = prompt(env)
        await env.press(1, confirm2, message_id=mid2)
        assert env.service.room_id_of(1) is None
    run(scenario())


def test_leaving_requires_fresh_confirmation_if_penalty_terms_changed(env):
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        rid = env.service.room_id_of(1)
        for uid in (2, 3):
            env.service.join_code(uid, f"Player{uid}", env.service.get(rid).code)
            env.service.act(uid, "rd:1")
        env.service.act(1, "ui:settings")
        env.service.act(1, "th:animals")
        await env.press(1, "rm:leave", message_id=700)
        mid, confirm = prompt(env)
        env.service.act(1, "start")
        await env.press(1, confirm, message_id=mid)
        assert env.service.room_id_of(1) == rid
        with env.packs._factory() as db:
            assert db.get(UserPenalty, 1) is None
        await env.press(1, "rm:leave", message_id=700)
        assert "блокировке" in env.session.of(SendMessage)[-1].text
        mid, confirm = prompt(env)
        await env.press(1, confirm, message_id=mid)
        assert env.service.room_id_of(1) is None
        with env.packs._factory() as db:
            assert db.get(UserPenalty, 1).strikes == 1
        await env.press(1, confirm, message_id=mid)
        with env.packs._factory() as db:
            assert db.get(UserPenalty, 1).strikes == 1
    run(scenario())


def test_confirmation_after_panel_reopen_is_rejected(env):
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        await env.press(1, "rm:leave", message_id=700)
        mid, confirm = prompt(env)
        await env.text(1, "Моя комната")
        current = env.service.panel_id_of(1)
        await env.press(1, confirm, message_id=mid)
        assert env.service.room_id_of(1) is not None and current not in deleted(env)
    run(scenario())


def test_private_callback_guard_rejects_groups_and_other_private_chats(env):
    async def scenario():
        called = []
        async def handler(event, data):
            called.append(True)
        for kind, cid in (("group", -100), ("private", 99)):
            call = CallbackQuery(id=str(cid), from_user=User(id=1, is_bot=False, first_name="A"), chat_instance="x", data="online:create", message=Message(message_id=7, date=datetime.now(), chat=Chat(id=cid, type=kind))).as_(env.bot)
            await PrivateCallbacksMiddleware()(handler, call, {})
        assert not called
    run(scenario())


def test_admin_rights_require_configured_numeric_id(env):
    settings = env.dp["settings"]
    assert is_admin(settings, 1, "renamed")
    assert not is_admin(settings, 2, settings.admin_username)
    assert not is_admin(replace(settings, admin_id=None), 1, settings.admin_username)
    async def scenario():
        env.dp["settings"] = replace(settings, admin_id=None)
        await env.press(1, "online:test:1", username=settings.admin_username)
        assert env.service.room_id_of(1) is None
    run(scenario())


def test_failed_invites_are_limited_before_database_queries_and_expire(env):
    now = [100.0]
    env.service.join_limiter.clock = lambda: now[0]
    room = env.service.create(1, "Host").room
    for _ in range(5):
        with pytest.raises(RoomError):
            env.service.join_code(2, "Guest", "000000")
    queries = []
    engine = env.packs._factory.kw["bind"]
    def count(*args): queries.append(True)
    event.listen(engine, "before_cursor_execute", count)
    try:
        with pytest.raises(RoomError, match="Слишком много"):
            env.service.join_token(2, "Guest", room.token)
        assert not queries
    finally:
        event.remove(engine, "before_cursor_execute", count)
    env.service.join_token(3, "Other", room.token)
    now[0] = 161
    env.service.join_token(2, "Guest", room.token)
    assert env.service.room_id_of(2) == env.service.room_id_of(1)
    assert env.service.join_limiter.wait_seconds(2) == 0


def test_invalid_join_input_does_not_reach_database(env):
    queries = []
    engine = env.packs._factory.kw["bind"]
    def count(*args): queries.append(True)
    event.listen(engine, "before_cursor_execute", count)
    try:
        for operation, value in ((env.service.join_code, "x" * 10000), (env.service.join_token, "a" * 10000)):
            with pytest.raises(RoomError):
                operation(2, "Guest", value)
        assert not queries
    finally:
        event.remove(engine, "before_cursor_execute", count)


def test_throttle_has_bounded_memory_and_separate_message_callback_limits(env):
    async def scenario():
        middleware = ThrottleMiddleware(interval=10, max_tracked=3)
        calls = []
        async def handler(event, data):
            calls.append(True)
        user = User(id=1, is_bot=False, first_name="A")
        call = CallbackQuery(id="x", from_user=user, chat_instance="x", data="noop").as_(env.bot)
        data = {"event_from_user": user}
        await middleware(handler, call, data)
        await middleware(handler, "message", data)
        assert len(calls) == 2
        for _ in range(20):
            await middleware(handler, call, data)
        assert len(env.session.of(AnswerCallbackQuery)) == 1
        for uid in range(2, 20):
            await middleware(handler, "message", {"event_from_user": User(id=uid, is_bot=False, first_name="A")})
        assert len(middleware._recent) <= 3 and len(middleware._notified) <= 3
    run(scenario())


def test_callback_burst_allows_four_presses_and_bounds_pending_actions(env, monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(throttle, "time", SimpleNamespace(monotonic=lambda: clock.now))

    async def scenario():
        middleware = ThrottleMiddleware()
        started, release = asyncio.Queue(), asyncio.Event()
        user = User(id=1, is_bot=False, first_name="A")

        def press(update_id, action):
            callback = CallbackQuery(
                id=str(update_id), from_user=user, chat_instance="x",
                inline_message_id="panel", data=action,
            )
            return Update.model_validate(
                {"update_id": update_id, "callback_query": callback.model_dump()},
                context={"bot": env.bot},
            )

        async def slow(event, data):
            started.put_nowait(None)
            await release.wait()
            return "handled"

        async def fast(event, data):
            return "handled"

        data = {"event_from_user": user}
        pending = [asyncio.create_task(middleware(slow, press(i, "rm:gu"), data)) for i in range(1, 5)]
        for _ in pending:
            await started.get()
        clock.now = 0.4
        assert await middleware(fast, press(5, "rm:gu"), data) is None
        clock.now = 1.1
        assert await middleware(fast, press(6, "menu:home"), data) is None
        release.set()
        assert await asyncio.gather(*pending) == ["handled"] * 4
        assert await middleware(fast, press(7, "rm:gu"), data) == "handled"
        assert len(env.session.of(AnswerCallbackQuery)) == 1

    run(scenario())


def test_confirmation_join_and_chat_caches_are_bounded(env):
    confirmations = LeaveConfirmations(max_entries=3)
    limiter = FailedJoinLimiter(clock=lambda: 10, max_users=3)
    for uid in range(10):
        confirmations.register(uid, 1, 2, 3, "text", "token")
        limiter.failed(uid)
    assert len(confirmations._items) == len(limiter._users) == 3
    for uid in range(5000):
        env.runtime.allow_chat_message(uid)
        env.runtime._defer_render(uid, 10, room_id=1)
    assert len(env.runtime._last_chat) <= 4096 and len(env.runtime._retry_after) <= 4096
    assert len(env.runtime._pending_rooms) <= 4096


def test_room_locks_keep_waiters_serialized_and_release_idle_entries(env):
    async def scenario():
        active = peak = 0
        async def worker():
            nonlocal active, peak
            async with env.runtime.lock(123):
                active += 1; peak = max(peak, active)
                gc.collect()
                await asyncio.sleep(0.01)
                active -= 1
        await asyncio.gather(*(worker() for _ in range(4)))
        assert peak == 1
    run(scenario())
    gc.collect()
    assert not env.runtime._locks


def test_non_ascii_nonce_cannot_crash_confirmation_processing():
    store = LeaveConfirmations()
    store.register(1, 2, 3, 4, "text", "valid-token")
    assert store.consume(1, 4, "подделка") is None
    assert store.consume(1, 4, "x" * 10000) is None
    assert store.consume(1, 4, "valid-token").room_id == 2


def test_sql_like_pack_title_and_search_are_data_not_commands(env):
    title = "O'Reilly -- набор"
    pid = env.packs.create_pack(7, "Автор'", title, ["кот", "пёс"])
    assert env.packs.info(7, pid).title == title
    rows, total = env.packs.list_packs(7, "my", query="' OR '1'='1")
    assert total == 0 and rows == []
    assert env.packs.info(7, pid).words_count == 2


def test_failed_panel_context_leaves_room_state_unchanged(env):
    update = env.service.create(1, "Host")
    env.service.set_panel(update.room_id, 1, 700)
    with pytest.raises(RoomError):
        env.service.act(1, "rd:0", expected_room_id=update.room_id, expected_panel_id=999)
    assert env.service.get(update.room_id).players[0]["ready"]
    with pytest.raises(RoomError):
        env.service.act(1, "leave_confirm", expected_room_id=999, expected_panel_id=700)
    assert env.service.room_id_of(1) == update.room_id
