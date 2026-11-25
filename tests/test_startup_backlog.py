import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from aiogram.types import CallbackQuery, Update, User

from app.controllers.telegram.backlog import replay_pending_updates
from app.controllers.telegram.middlewares.throttle import ThrottleMiddleware
from app.main import poll


def test_startup_replays_latest_button_per_player_across_batches():
    def update(update_id, user_id=None):
        callback = SimpleNamespace(from_user=SimpleNamespace(id=user_id)) if user_id else None
        return SimpleNamespace(update_id=update_id, callback_query=callback)

    message_before = update(1)
    second_player = update(50, user_id=2)
    first_batch = [update(i, user_id=1) for i in range(1, 101)]
    first_batch[0] = message_before
    first_batch[49] = second_player
    message_between = update(101)
    last_button = update(102, user_id=1)
    bot = SimpleNamespace(get_updates=AsyncMock(
        side_effect=[first_batch, [message_between, last_button], []],
    ))
    dispatcher = SimpleNamespace(
        resolve_used_update_types=lambda: ["message", "callback_query"],
        feed_update=AsyncMock(),
    )

    asyncio.run(replay_pending_updates(bot, dispatcher))

    assert [call.kwargs["offset"] for call in bot.get_updates.await_args_list] == [None, 101, 103]
    assert [call.args[1] for call in dispatcher.feed_update.await_args_list] == [
        message_before, second_player, message_between, last_button,
    ]
    assert all(call.kwargs["startup_backlog"] for call in dispatcher.feed_update.await_args_list)


def test_startup_dispatches_queued_button_before_any_room_tick(tmp_path, monkeypatch):
    user = SimpleNamespace(id=1)
    button = SimpleNamespace(update_id=42, callback_query=SimpleNamespace(from_user=user))
    bot = SimpleNamespace(session=SimpleNamespace(middleware=Mock(), close=AsyncMock()))
    runtime = SimpleNamespace(close=AsyncMock())
    dispatcher = SimpleNamespace(resolve_used_update_types=lambda: ["callback_query"])
    monkeypatch.setattr("app.main.health_path", lambda: tmp_path / "health.json")
    monkeypatch.setattr("app.main.monitor_health", AsyncMock())

    async def scenario():
        order = []
        timer_ran = asyncio.Event()
        responses = iter([[button], []])

        async def get_updates(**kwargs):
            await asyncio.sleep(0)
            return next(responses)

        async def feed_update(bot, update, *, startup_backlog):
            assert startup_backlog and update == button
            await asyncio.sleep(0)
            order.append("button")

        async def tick():
            order.append("timer")
            timer_ran.set()

        async def start_polling(bot, *, close_bot_session):
            await timer_ran.wait()

        bot.get_updates = get_updates
        dispatcher.feed_update = feed_update
        dispatcher.start_polling = start_polling
        runtime.run_ticker = tick
        await poll(bot, dispatcher, runtime, None)
        assert order == ["button", "timer"]

    asyncio.run(scenario())
    bot.session.close.assert_awaited_once()


def test_startup_button_passes_throttle_without_changing_live_limits(monkeypatch):
    monkeypatch.setattr("app.controllers.telegram.middlewares.throttle.time", SimpleNamespace(monotonic=lambda: 1))
    user = User(id=1, is_bot=False, first_name="Player")
    button = CallbackQuery(
        id="42", from_user=user, chat_instance="chat", data="rm:rd:1",
        inline_message_id="button-panel",
    )
    event = Update(update_id=42, callback_query=button)
    middleware = ThrottleMiddleware()
    handler = AsyncMock()
    monkeypatch.setattr("app.controllers.telegram.middlewares.throttle.safe_answer", AsyncMock())

    async def scenario():
        live_data = {"event_from_user": user}
        await middleware(handler, event, {**live_data, "startup_backlog": True})
        for _ in range(5):
            await middleware(handler, event, live_data)

    asyncio.run(scenario())
    assert handler.await_count == 5
    assert handler.await_args_list[0].args[1]["startup_backlog"]
