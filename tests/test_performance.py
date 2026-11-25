import asyncio
from datetime import datetime
from sqlalchemy import event
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import CallbackQuery, User

from app.controllers.telegram.runtime import OnlineRuntime
from app.models.database.worker import run_db
from app.controllers.telegram.middlewares.track import TrackScreensMiddleware
from app.models.database.models import GameSession, SessionPlayer, MatchRound, RoundPlayer
from app.models.services.history_service import HistoryService
from tests.test_bot_flow import env, run


def count_sql(factory):
    statements = []
    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    engine = factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", count)
    return statements, lambda: event.remove(engine, "before_cursor_execute", count)


def make_panels(env, users=4):
    update = env.service.create(1, "Host")
    for uid in range(2, users + 1):
        env.service.join_code(uid, f"Player{uid}", update.room.code)
    for uid in range(1, users + 1):
        env.service.set_panel(update.room_id, uid, 700 + uid)
    return update.room_id


def test_game_callback_has_no_tracking_database_queries(env):
    statements, stop = count_sql(env.packs._factory)
    async def scenario():
        call = CallbackQuery(id="1", from_user=User(id=1, is_bot=False, first_name="Host"), chat_instance="x", data="rm:noop")
        called = []
        async def handler(event, data):
            called.append(True)
            return "handled"
        result = await TrackScreensMiddleware(env.tracker)(handler, call, {"event_from_user": call.from_user, "repo": env.local_repo, "runtime": env.runtime})
        assert result == "handled" and called
    try:
        run(scenario())
        assert not statements
    finally:
        stop()


def test_catalog_query_count_is_independent_of_page_size(env):
    statements, stop = count_sql(env.packs._factory)
    try:
        for size in (1, 6, 8):
            statements.clear()
            rows, total = env.packs.list_packs(1, "builtin", size=size, sort="size")
            assert len(rows) == size and total == 8
            assert len(statements) == 2
            assert [row.words_count for row in rows] == [20] * size
    finally:
        stop()


def test_active_panel_refresh_reads_only_the_saved_room(env):
    rid = make_panels(env, users=3)
    for uid in (2, 3):
        env.service.act(uid, "rd:1")
    env.service.act(1, "th:animals")
    env.service.act(1, "start")
    env.service.act(1, "force")
    statements, stop = count_sql(env.packs._factory)
    try:
        run(env.runtime.refresh(rid))
        assert len(statements) == 1
        assert len(env.session.of(EditMessageText)) == 3
        assert all("Обсуждение" in edit.text for edit in env.session.of(EditMessageText))
    finally:
        stop()


def test_player_can_change_state_while_previous_panel_edit_is_in_flight(env, monkeypatch):
    rid = make_panels(env, users=3)
    original_request = env.session.make_request
    original_act = env.service.act

    async def scenario():
        await env.runtime.refresh(rid)
        editing = asyncio.Event()
        release = asyncio.Event()
        third_player_acted = asyncio.Event()
        loop = asyncio.get_running_loop()

        async def request(bot, method, timeout=None):
            if isinstance(method, EditMessageText) and not editing.is_set():
                editing.set()
                await release.wait()
            return await original_request(bot, method, timeout)

        def act(uid, *args, **kwargs):
            update = original_act(uid, *args, **kwargs)
            if uid == 3:
                loop.call_soon_threadsafe(third_player_acted.set)
            return update

        monkeypatch.setattr(env.session, "make_request", request)
        monkeypatch.setattr(env.service, "act", act)
        first = asyncio.create_task(env.press(2, "rm:rd:1", message_id=702))
        second = None
        try:
            await asyncio.wait_for(editing.wait(), 5)
            second = asyncio.create_task(env.press(3, "rm:rd:1", message_id=703))
            await asyncio.wait_for(third_player_acted.wait(), 5)
            room = await run_db(env.service.get, rid)
            assert all(player["ready"] for player in room.players)
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second else []))
        host_panels = [edit for edit in env.session.of(EditMessageText) if edit.chat_id == 1]
        assert "готовы 3/3" in host_panels[-1].text

    run(scenario())


def test_timer_advances_again_while_room_panel_delivery_is_stalled(env, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(env.service, "_clock", lambda: clock[0])
    rid = make_panels(env, users=3)
    for uid in (2, 3):
        env.service.act(uid, "rd:1")
    env.service.act(1, "th:animals")
    env.service.act(1, "start")
    initial = env.service.act(1, "force").room
    original_request = env.session.make_request

    async def scenario():
        editing = asyncio.Event()
        release = asyncio.Event()

        async def request(bot, method, timeout=None):
            if isinstance(method, EditMessageText) and not editing.is_set():
                editing.set()
                await release.wait()
            return await original_request(bot, method, timeout)

        monkeypatch.setattr(env.session, "make_request", request)
        try:
            clock[0] += 100
            await env.runtime.tick(background=True)
            await asyncio.wait_for(editing.wait(), 5)
            clock[0] += 100
            await asyncio.wait_for(env.runtime.tick(background=True), 5)
            room = await run_db(env.service.get, rid)
            assert room.round.current() == initial.round.order[2]
        finally:
            release.set()
            await asyncio.wait_for(
                asyncio.gather(*env.runtime._deliveries.values()), 5,
            )

    run(scenario())


def test_settings_navigation_needs_only_two_identity_queries(env):
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        await env.text(1, "Настройки")
        env.user_settings.prefs(1)  # warm the existing settings cache
        statements, stop = count_sql(env.packs._factory)
        try:
            await env.press(1, "set:menu", message_id=env.tracker.get(1))
            assert len(statements) == 2
            assert not any("pack_words" in s for s in statements)
        finally:
            stop()
    run(scenario())


def test_panel_cache_skips_identical_edits_but_updates_changed_preferences(env):
    rid = make_panels(env)
    runtime = OnlineRuntime(env.bot, env.service, "testbot", prefs_for=env.user_settings.prefs)
    async def scenario():
        await runtime.refresh(rid)
        assert len(env.session.of(EditMessageText)) == 4
        await runtime.refresh(rid)
        assert len(env.session.of(EditMessageText)) == 4
        env.user_settings.set_emoji(1, True)
        await runtime.refresh(rid)
        assert len(env.session.of(EditMessageText)) == 5
        assert env.session.of(EditMessageText)[-1].chat_id == 1
        await runtime.adopt_panel(rid, 1, 701)
        await runtime.refresh(rid)
        assert len(env.session.of(EditMessageText)) == 6
    run(scenario())


def test_panel_requests_are_parallel_and_bounded(env):
    rid = make_panels(env, users=6)
    runtime = OnlineRuntime(env.bot, env.service, "testbot")
    original = env.session.make_request
    active = peak = 0
    async def request(bot, method, timeout=None):
        nonlocal active, peak
        if not isinstance(method, EditMessageText):
            return await original(bot, method, timeout)
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.02)
            return await original(bot, method, timeout)
        finally:
            active -= 1
    env.session.make_request = request
    run(runtime.refresh(rid))
    assert 1 < peak <= 4
    assert len(env.session.of(EditMessageText)) == 6


def test_retry_after_does_not_create_duplicate_panel_or_retry_too_soon(env):
    rid = make_panels(env, users=1)
    runtime = OnlineRuntime(env.bot, env.service, "testbot")
    env.session.fail_on[EditMessageText] = lambda method: TelegramRetryAfter(method=method, message="Too many requests", retry_after=30)
    async def scenario():
        await runtime.refresh(rid)
        assert len(env.session.of(EditMessageText)) == 1
        await runtime.refresh(rid)
        assert len(env.session.of(EditMessageText)) == 1
        assert not env.session.of(SendMessage)
        env.session.fail_on.clear()
        runtime._retry_after[1] = 0
        await runtime.refresh(rid)
        assert len(env.session.of(EditMessageText)) == 2
    run(scenario())


def test_missing_panel_is_recreated_after_cache_probe(env):
    rid = make_panels(env, users=1)
    runtime = OnlineRuntime(env.bot, env.service, "testbot")
    async def scenario():
        await runtime.refresh(rid)
        runtime.cache_ttl = 0
        env.session.fail_on[EditMessageText] = lambda method: TelegramBadRequest(method=method, message="message to edit not found")
        await runtime.refresh(rid)
        new_id = env.service.panel_id_of(1)
        assert new_id != 701 and len(env.session.of(SendMessage)) == 1
        env.session.fail_on.clear()
        runtime.cache_ttl = 60
        edits = len(env.session.of(EditMessageText))
        await runtime.refresh(rid)
        assert len(env.session.of(EditMessageText)) == edits
    run(scenario())


def test_invalid_edit_does_not_send_another_panel(env):
    rid = make_panels(env, users=1)
    runtime = OnlineRuntime(env.bot, env.service, "testbot")
    env.session.fail_on[EditMessageText] = lambda method: TelegramBadRequest(method=method, message="can't parse entities")
    run(runtime.refresh(rid))
    assert not env.session.of(SendMessage)


def test_history_bulk_loading_has_constant_query_count_and_correct_players(env):
    factory = env.packs._factory
    with factory.begin() as db:
        for i in range(5):
            session = GameSession(room_code=str(i), title=f"Lobby{i}", host_id=1, status="finished")
            db.add(session); db.flush()
            for uid in (1, 2):
                db.add(SessionPlayer(session_id=session.id, user_id=uid, name=f"Player{uid}", score=uid))
            for n in range(1, 6):
                match = MatchRound(session_id=session.id, round_no=n, theme="animals", word="cat", spy_user_id=2, spy_name="Player2", outcome="spy_win_guess")
                db.add(match); db.flush()
                for uid in (1, 2):
                    db.add(RoundPlayer(round_id=match.id, user_id=uid, name=f"Player{uid}", role="spy" if uid == 2 else "civilian", result="win", score_delta=uid))
    history = HistoryService(factory)
    statements, stop = count_sql(factory)
    try:
        rows, total = history.user_lobbies(1, 0, 5)
        assert len(statements) == 4
        assert total == len(rows) == 5
        assert all(r.players == [("Player2", 2), ("Player1", 1)] and r.rounds_count == 5 for r in rows)
        assert all(set(r.player_ids) == {(1, "Player1"), (2, "Player2")} for r in rows)
        statements.clear()
        rounds, total = history.lobby_rounds(1, rows[0].id, 0, 5)
        assert len(statements) == 4 and total == len(rounds) == 5
        assert all(r.scores == [("Player2", "spy", 2), ("Player1", "civilian", 1)] for r in rounds)
        assert history.lobby_rounds(999, rows[0].id, 0, 5) is None
    finally:
        stop()


def test_running_room_loads_selected_packs_not_entire_library(env):
    from tests.test_history import new_room
    room_id = new_room(env.service, n=3)
    env.service.act(1, "start")
    original_full = env.packs.library_for
    original_selected = env.packs.packs_by_slugs
    calls = []
    def full(uid):
        calls.append("full")
        return original_full(uid)
    def selected(slugs):
        calls.append(tuple(slugs))
        return original_selected(slugs)
    env.packs.library_for = full
    env.packs.packs_by_slugs = selected
    room = env.service.get(room_id)
    assert room.phase == "dealing"
    assert calls == [("animals",)]
    env.service.act(1, "role")
    assert room.round.word
