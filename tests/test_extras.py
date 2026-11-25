from app.views.telegram.emoji import strip_decor
from tests.pack_fixtures import test_library
import asyncio
import random
from datetime import datetime, timedelta, timezone

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from sqlalchemy import update

from app.views.telegram import settings_view
from app.views.telegram.history_view import fmt_dt, fmt_short, tz_label
from app.controllers.telegram.middlewares.throttle import ThrottleMiddleware
from app.models.database.models import LocalGameRecord
from app.models.database.repository import LocalGameRepository
from app.models.database.session import make_session_factory
from app.models.game.packs import Pack, PackLibrary
from app.models.game.room import THEMES_PAGE, RoomError
from app.models.game.session import LocalSession
from app.models.services.room_service import RoomService
from app.models.services.settings_service import OFFSETS, SettingsService
from tests.test_bot_flow import Env, run  # noqa: F401


class FakeUser:
    def __init__(self, uid):
        self.id = uid


class FakeCallback:
    def __init__(self):
        self.answered = 0

    async def answer(self, *args, **kwargs):
        self.answered += 1


def test_throttle_blocks_rapid_events_per_user():
    async def scenario():
        mw = ThrottleMiddleware(interval=5)
        calls = []

        async def handler(event, data):
            calls.append(event)
            return "ok"

        results = [await mw(handler, i, {"event_from_user": FakeUser(1)}) for i in range(5)]
        other = await mw(handler, "other", {"event_from_user": FakeUser(2)})
        return results, other, calls

    results, other, calls = asyncio.run(scenario())
    assert results == ["ok"] * 4 + [None]
    assert other == "ok" and calls == [0, 1, 2, 3, "other"]


def test_throttle_disabled_and_no_user():
    async def scenario():
        mw = ThrottleMiddleware(interval=0)
        results = [await mw(lambda e, d: asyncio.sleep(0, "ok"), "x", {"event_from_user": FakeUser(1)}) for _ in range(3)]
        no_user = await ThrottleMiddleware(5)(lambda e, d: asyncio.sleep(0, "ok"), "x", {})
        return results, no_user

    results, no_user = asyncio.run(scenario())
    assert results == ["ok"] * 3 and no_user == "ok"


def test_throttle_prunes_old_entries():
    async def scenario():
        mw = ThrottleMiddleware(interval=0.0001, max_tracked=3)
        for uid in range(10):
            await mw(lambda e, d: asyncio.sleep(0, "ok"), "x", {"event_from_user": FakeUser(uid)})
        return len(mw._recent)

    assert asyncio.run(scenario()) <= 3


def test_throttle_integration_allows_four_fast_messages(tmp_path):
    env = Env(tmp_path)
    env.dp.message.outer_middleware(ThrottleMiddleware(interval=30))

    async def scenario():
        for _ in range(5):
            await env.text(7, "Профиль")

    run(scenario())
    messages = [c for c in env.session.calls if isinstance(c, (SendMessage, EditMessageText))]
    assert len([c for c in messages if c.text.startswith("Профиль")]) == 4


def many_packs(n=20):
    extra = [Pack(id=f"x{i}", title=f"Тема {i:02d}", description="", category="К", words=("а", "б")) for i in range(n)]
    return test_library().with_packs(extra)


def test_local_theme_selection_is_paginated():
    s = LocalSession(rng=random.Random(1), library=many_packs())
    s.act(s.seq, "n:3")
    for name in ["А", "Б", "В"]:
        s.handle_text(name)
    s.act(s.seq, "th")
    pack_rows = [a for row in s.screen().rows for _, a in row if a.startswith("t:")]
    assert len(pack_rows) == 8
    assert "tp:1" in [a for row in s.screen().rows for _, a in row]
    s.act(s.seq, "tp:3")
    last = [a for row in s.screen().rows for _, a in row if a.startswith("t:")]
    assert len(last) == 28 - 24 and "tp:2" in [a for row in s.screen().rows for _, a in row]
    s.act(s.seq, "t:x19")
    assert s.themes == ["x19"]
    assert s.to_dict()["theme_page"] == 3


def test_room_theme_selection_is_paginated_and_host_only(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path}/t.db")
    service = RoomService(factory, library=many_packs())
    upd = service.create(1, "Хост")
    service.join_code(2, "Б", upd.room.code)
    service.act(1, "ui:themes")
    room = service.get(upd.room_id)
    actions = [a for row in room.view_for(1, "bot", 0).rows for _, a in row]
    assert len([a for a in actions if a.startswith("th:")]) == THEMES_PAGE and "tp:1" in actions
    with pytest.raises(RoomError):
        service.act(2, "tp:1")
    service.act(1, "tp:3")
    room = service.get(upd.room_id)
    last = [a for row in room.view_for(1, "bot", 0).rows for _, a in row if a.startswith("th:")]
    assert len(last) == 28 - 24
    service.act(1, "th:x19")
    assert service.get(upd.room_id).settings["themes"] == ["x19"]


def test_countdown_refreshes_active_rooms_periodically(tmp_path):
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

    async def scenario():
        before = await env.runtime.countdown(now=100.0)
        for uid in (1, 2, 3):
            service.act(uid, "role")
        started = await env.runtime.countdown(now=100.0)
        soon = await env.runtime.countdown(now=104.9)
        later = await env.runtime.countdown(now=120.0)
        return before, started, soon, later

    before, started, soon, later = run(scenario())
    assert before == 0 and started == 1 and soon == 0 and later == 1
    assert env.session.of(EditMessageText) or env.session.of(SendMessage)


def test_active_room_ids_only_with_deadline(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path}/a.db")
    service = RoomService(factory, rng=random.Random(1))
    upd = service.create(1, "Хост")
    assert service.active_room_ids() == []
    for uid in (2, 3):
        service.join_code(uid, f"И{uid}", upd.room.code)
    service.act(1, "ui:settings")
    service.act(1, "th:animals")
    service.act(2, "rd:1")
    service.act(3, "rd:1")
    service.act(1, "start")
    for uid in (1, 2, 3):
        service.act(uid, "role")
    assert service.active_room_ids() == [upd.room_id]


def test_local_game_purge_stale(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path}/l.db")
    repo = LocalGameRepository(factory)
    repo.save(1, {"a": 1})
    repo.save(2, {"a": 2})
    old = datetime.now(timezone.utc) - timedelta(days=40)
    with factory.begin() as db:
        db.execute(update(LocalGameRecord).where(LocalGameRecord.owner_id == 1).values(updated_at=old))
    assert repo.purge_stale(30) == 1
    assert repo.load(1) is None and repo.load(2) == {"a": 2}
    assert repo.purge_stale(30) == 0


def test_timezone_formatting():
    naive = datetime(2026, 10, 17, 12, 0)
    aware = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
    assert fmt_dt(naive) == "17.10.2026 12:00 (UTC)"
    assert fmt_dt(naive, 180) == "17.10.2026 15:00 (UTC+3)"
    assert fmt_dt(aware, 180) == fmt_dt(naive, 180)
    assert fmt_dt(naive, -300) == "17.10.2026 07:00 (UTC-5)"
    assert fmt_short(naive, 600) == "17.10 22:00"
    assert fmt_dt(None) == "-" and tz_label(0) == "UTC"


def test_settings_service_and_screens(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path}/s.db")
    svc = SettingsService(factory)
    assert svc.tz_minutes(1) == 0
    svc.set_tz_hours(1, 3)
    svc.set_tz_hours(1, 5)
    assert svc.tz_minutes(1) == 300 and svc.tz_minutes(2) == 0
    with pytest.raises(ValueError):
        svc.set_tz_hours(1, 99)
    assert "Москва (UTC+3)" in settings_view.settings_screen(180).text
    picker = settings_view.timezone_screen(180)
    labels = [label for row in picker.rows for label, _ in row]
    assert any(label.startswith("✓ ") and "UTC+3" in label for label in labels)
    assert len(OFFSETS) == len([a for row in picker.rows for _, a in row if a.startswith("set:tzset:")])


def test_settings_flow_through_bot(tmp_path):
    env = Env(tmp_path)

    async def scenario():
        await env.text(5, "Настройки")
        await env.press(5, "set:tzmenu", message_id=env.tracker.get(5))
        await env.press(5, "set:tzset:3", message_id=env.tracker.get(5))
        await env.press(5, "set:tzset:99", message_id=env.tracker.get(5))

    run(scenario())
    assert env.user_settings.tz_minutes(5) == 180
    assert any(c.text == "Недопустимый часовой пояс" for c in env.session.of(AnswerCallbackQuery) if c.text)
    assert any(strip_decor(c.text).startswith("Настройки") for c in env.session.of(SendMessage))


def test_throttle_first_event_passes_even_right_after_boot(monkeypatch):
    import time as time_module

    monkeypatch.setattr(time_module, "monotonic", lambda: 0.5)

    async def scenario():
        mw = ThrottleMiddleware(interval=30)
        return [
            await mw(lambda e, d: asyncio.sleep(0, "ok"), "x", {"event_from_user": FakeUser(1)})
            for _ in range(5)
        ]

    assert asyncio.run(scenario()) == ["ok"] * 4 + [None]
