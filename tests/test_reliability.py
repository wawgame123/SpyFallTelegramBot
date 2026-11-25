import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import EditMessageText, SendMessage, GetUpdates, AnswerCallbackQuery

from app.models.database.worker import run_db
from app.health import check_health, monitor_health, PollingHealthMiddleware, write_status
from tests.test_bot_flow import env, run
from tests.test_performance import make_panels


def test_slow_database_handler_does_not_block_event_loop(env):
    started, release = threading.Event(), threading.Event()
    original = env.dp['stats'].profile
    caller = threading.get_ident()
    workers = []
    def slow_profile(uid):
        workers.append(threading.get_ident())
        started.set()
        assert release.wait(5)
        return original(uid)
    env.dp['stats'].profile = slow_profile
    async def scenario():
        handler = asyncio.create_task(env.text(1, 'Профиль'))
        try:
            while not started.is_set():
                await asyncio.sleep(0.001)
            # A Telegram acknowledgement can finish while SQL is still blocked.
            await env.bot.answer_callback_query('independent')
            assert env.session.of(AnswerCallbackQuery)
            assert not handler.done()
        finally:
            release.set()
            await handler
    run(asyncio.wait_for(scenario(), 8))
    assert workers and all(t != caller for t in workers)
    assert 'Профиль' in env.last_text()


def test_cancelled_database_job_finishes_before_next_job_and_propagates_errors():
    started, release = threading.Event(), threading.Event()
    order = []
    def first():
        started.set()
        assert release.wait(5)
        order.append('commit')
    async def scenario():
        task = asyncio.create_task(run_db(first))
        try:
            while not started.is_set():
                await asyncio.sleep(0.001)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            second = asyncio.create_task(run_db(lambda: order.append('next')))
            await asyncio.sleep(0.01)
            assert not order
        finally:
            release.set()
        await second
        def fail():
            raise ValueError('test rollback')
        with pytest.raises(ValueError, match='rollback'):
            await run_db(fail)
        assert await run_db(lambda: 42) == 42
    run(scenario())
    assert order == ['commit', 'next']


@pytest.mark.parametrize('failure', ['limit', 'network'])
@pytest.mark.parametrize('phase', ['lobby', 'result', 'final'])
def test_deferred_panel_retries_without_player_action_in_untimed_phases(env, phase, failure):
    from tests.test_history import new_room, play_round
    rid = new_room(env.service, n=3)
    for uid in (1, 2, 3):
        env.service.set_panel(rid, uid, 700 + uid)
    if phase != 'lobby':
        play_round(env.service, rid)
        if phase == 'final':
            env.service.act(1, 'end')
    assert env.service.get(rid).phase == phase
    assert rid not in env.service.active_room_ids()
    error = (lambda method: TelegramRetryAfter(method=method, message='limited', retry_after=30)) if failure == 'limit' else (lambda method: TelegramNetworkError(method=method, message='offline'))
    env.session.fail_on[EditMessageText] = error
    async def scenario():
        await env.runtime.refresh(rid)
        assert 1 in env.runtime._pending_rooms
        await env.runtime.retry_pending()
        assert len(env.session.of(EditMessageText)) == 3
        env.session.fail_on.clear()
        env.runtime._retry_after.update({uid: 0 for uid in (1, 2, 3)})
        await env.runtime.retry_pending()
        assert len(env.session.of(EditMessageText)) == 6
        assert not env.runtime._pending_rooms
        assert not env.session.of(SendMessage)
    run(scenario())


def test_retry_loads_current_state_and_drops_closed_room(env):
    rid = make_panels(env, users=1)
    env.session.fail_on[EditMessageText] = lambda method: TelegramNetworkError(method=method, message='offline')
    async def scenario():
        await env.runtime.refresh(rid)
        await run_db(env.service.act, 1, 'ui:settings')
        env.session.fail_on.clear()
        env.runtime._retry_after[1] = 0
        await env.runtime.retry_pending()
        assert 'Настройки' in env.session.of(EditMessageText)[-1].text
        env.runtime._defer_render(1, 1, rid)
        await run_db(env.service.act, 1, 'leave_confirm')
        env.runtime._retry_after[1] = 0
        count = len(env.session.calls)
        await env.runtime.retry_pending()
        assert not env.runtime._pending_rooms
        assert len(env.session.calls) == count
    run(scenario())


@pytest.mark.parametrize('kind', [TelegramForbiddenError, TelegramBadRequest])
def test_permanent_edit_failure_stops_previous_retry(env, kind):
    rid = make_panels(env, users=1)
    env.runtime._defer_render(1, 1, rid)
    env.runtime._retry_after[1] = 0
    env.session.fail_on[EditMessageText] = lambda method: kind(method=method, message="can't parse entities")
    run(env.runtime.retry_pending())
    assert not env.runtime._pending_rooms
    assert not env.session.of(SendMessage)


def test_health_rejects_missing_stale_corrupt_and_failed_heartbeat(tmp_path):
    path = tmp_path/'health.json'
    assert not check_health(path)[0]
    path.write_text('broken')
    assert not check_health(path)[0]
    write_status(path, True, 'ok')
    status = json.loads(path.read_text())
    assert check_health(path, now=status['updated_at'] + 29)[0]
    assert not check_health(path, now=status['updated_at'] + 31)[0]
    assert not check_health(path, now=status['updated_at'] - 10)[0]
    write_status(path, False, 'database probe failed')
    assert check_health(path)[1] == 'database probe failed'


@pytest.mark.parametrize('failure', ['none', 'database', 'ticker', 'polling'])
def test_health_monitor_checks_database_timer_and_polling(env, tmp_path, failure):
    path = tmp_path/'health.json'
    async def scenario():
        ticker = asyncio.create_task(asyncio.sleep(60))
        runtime = SimpleNamespace(last_cycle=time.monotonic())
        polling = SimpleNamespace(last_poll=time.monotonic())
        factory = env.packs._factory
        if failure == 'database':
            def factory():
                raise OSError('offline')
        elif failure == 'ticker':
            ticker.cancel()
            await asyncio.gather(ticker, return_exceptions=True)
        elif failure == 'polling':
            polling.last_poll -= 121
        monitor = asyncio.create_task(monitor_health(factory, runtime, ticker, polling, path, interval=0.01))
        try:
            while not path.exists():
                await asyncio.sleep(0.001)
            assert check_health(path)[0] == (failure == 'none')
        finally:
            monitor.cancel()
            ticker.cancel()
            await asyncio.gather(monitor, ticker, return_exceptions=True)
    run(asyncio.wait_for(scenario(), 5))


def test_polling_health_only_advances_after_successful_long_poll():
    middleware = PollingHealthMiddleware()
    async def scenario():
        middleware.last_poll = 0
        async def fail(bot, method):
            raise OSError('offline')
        with pytest.raises(OSError):
            await middleware(fail, None, GetUpdates())
        assert middleware.last_poll == 0
        async def success(bot, method):
            return []
        assert await middleware(success, None, GetUpdates()) == []
        assert middleware.last_poll > 0
    run(scenario())

@pytest.mark.parametrize('callback', ['set:menu', 'hist:menu', 'pk:menu', 'lg:1:show'])
def test_non_room_screens_retry_edits_and_use_current_preferences(env, callback):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from app.controllers.telegram.recovery import EditRecoveryMiddleware
    from app.controllers.telegram.middlewares.button_style import ButtonStyleMiddleware
    recovery = EditRecoveryMiddleware()
    env.bot.session.middleware(recovery)
    env.bot.session.middleware(ButtonStyleMiddleware(env.user_settings.prefs))
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Настройки', callback_data=callback)]])
    env.session.fail_on[EditMessageText] = lambda method: TelegramRetryAfter(method=method, message='limited', retry_after=30)
    async def scenario():
        with pytest.raises(TelegramRetryAfter):
            await env.bot.edit_message_text('new screen', chat_id=1, message_id=701, reply_markup=markup)
        await recovery.retry_pending(env.bot)
        assert len(env.session.of(EditMessageText)) == 1
        await run_db(env.user_settings.set_emoji, 1, True)
        env.session.fail_on.clear()
        for key, (_, method) in list(recovery.pending.items()):
            recovery.pending[key] = (0, method)
        await recovery.retry_pending(env.bot)
        assert not recovery.pending
        edited = env.session.of(EditMessageText)[-1]
        assert edited.text == 'new screen'
        assert edited.reply_markup.inline_keyboard[0][0].text == '⚙️ Настройки'
        assert not env.session.of(SendMessage)
    run(scenario())


@pytest.mark.parametrize('next_action', ['edit', 'delete', 'online'])
def test_new_navigation_cancels_older_deferred_screen(env, next_action):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from app.controllers.telegram.recovery import EditRecoveryMiddleware
    recovery = EditRecoveryMiddleware()
    env.bot.session.middleware(recovery)
    env.session.fail_on[EditMessageText] = lambda method: TelegramNetworkError(method=method, message='offline')
    async def scenario():
        with pytest.raises(TelegramNetworkError):
            await env.bot.edit_message_text('old', chat_id=1, message_id=701)
        assert recovery.pending
        env.session.fail_on.clear()
        if next_action == 'delete':
            await env.bot.delete_message(chat_id=1, message_id=701)
        else:
            markup = None
            if next_action == 'online':
                markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Готов', callback_data='rm:rd:1')]])
                env.session.fail_on[EditMessageText] = lambda method: TelegramNetworkError(method=method, message='offline')
                with pytest.raises(TelegramNetworkError):
                    await env.bot.edit_message_text('room', chat_id=1, message_id=701, reply_markup=markup)
            else:
                await env.bot.edit_message_text('new', chat_id=1, message_id=701)
        assert not recovery.pending
        count = len(env.session.calls)
        await recovery.retry_pending(env.bot)
        assert len(env.session.calls) == count
    run(scenario())
