import json
import struct
from datetime import datetime, timezone
import pytest
from sqlalchemy import DateTime, bindparam, create_engine, inspect, select, text
from aiogram.methods import SendMessage

from app.database.models import GameSession, QuestionPack, RoomRecord
from app.database.migrate import run_migrations
from app.services.pack_service import PackError, MIN_WORDS
from tests.test_bot_flow import env, run


def choose_custom_pack(env, extra=False):
    pid = env.packs.create_pack(1, "Host", "Удаляемый пак", [f"слово{i}" for i in range(10)])
    slug = env.packs.info(1, pid).slug
    update = env.service.create(1, "Host")
    env.service.set_panel(update.room_id, 1, 700)
    env.service.act(1, "ui:themes")
    env.service.act(1, f"th:{slug}")
    if extra:
        env.service.act(1, "th:animals")
    env.service.act(1, "ui:lobby")
    return pid, slug, update.room_id


def test_reopening_lobby_after_owner_deletes_selected_pack(env):
    pid, slug, rid = choose_custom_pack(env)
    env.packs.delete_pack(1, pid)
    async def scenario():
        await env.text(1, "Моя комната")
        room = env.service.get(rid)
        assert room.settings["themes"] == []
        assert room.themes_notice
        assert "Выберите замену" in env.last_text()
        assert "не выбраны" in env.last_text()
        with env.packs._factory() as db:
            state = json.loads(db.get(RoomRecord, rid).state)
            assert state["settings"]["themes"] == []
        # Reopening again must not resurrect the deleted choice or break the UI.
        await env.text(1, "Моя комната")
        assert env.service.get(rid).settings["themes"] == []
        env.service.act(1, "ui:themes")
        env.service.act(1, "th:animals")
        assert not env.service.get(rid).themes_notice
    run(scenario())


def test_deleted_theme_is_removed_without_losing_valid_selections(env):
    pid, slug, rid = choose_custom_pack(env, extra=True)
    env.packs.delete_pack(1, pid)
    room = env.service.get(rid)
    assert room.settings["themes"] == ["animals"]
    for action in ("ui:settings", "ui:themes", "ui:lobby"):
        env.service.act(1, action)
        assert env.service.get(rid).view_for(1, "testbot", 0).text


def test_deleting_pack_does_not_destroy_running_round_snapshot(env):
    pid, slug, rid = choose_custom_pack(env)
    for uid in (2, 3):
        env.service.join_code(uid, f"Player{uid}", env.service.get(rid).code)
        env.service.act(uid, "rd:1")
    env.service.act(1, "start")
    for uid in (1, 2, 3):
        env.service.act(uid, "role")
    word = env.service.get(rid).round.word
    env.packs.delete_pack(1, pid)
    room = env.service.get(rid)
    assert room.round.word == word
    assert room.themes_label() == "не выбраны"
    for uid in (1, 2, 3):
        assert room.view_for(uid, "testbot", 0).text


def test_timer_and_automatic_panel_updates_cannot_keep_abandoned_room_alive(env):
    from tests.test_history import new_room, play_round
    now = [1_700_000_000.0]
    env.service._clock = lambda: now[0]
    rid = new_room(env.service, n=3)
    room = play_round(env.service, rid)
    sid = room.session_id
    env.service.act(1, "next")
    for uid in (1, 2, 3):
        env.service.act(uid, "role")
    with env.packs._factory() as db:
        original_activity = db.get(RoomRecord, rid).last_activity_at
    now[0] += 2 * 86400
    env.service.tick_room(rid)
    env.service.set_panel(rid, 1, 999)
    with env.packs._factory() as db:
        assert db.get(RoomRecord, rid).last_activity_at == original_activity
    assert env.service.purge_stale() == 1
    assert env.service.get(rid) is None
    with env.packs._factory() as db:
        assert db.get(GameSession, sid).status == "abandoned"


def test_real_player_activity_renews_room_expiration(env):
    now = [1_700_000_000.0]
    env.service._clock = lambda: now[0]
    update = env.service.create(1, "Host")
    now[0] += 2 * 86400
    env.service.act(1, "noop")
    assert env.service.purge_stale() == 0
    now[0] += 2 * 86400
    env.service.set_panel(update.room_id, 1, 700, player_activity=True)
    assert env.service.purge_stale() == 0


def test_public_pack_cannot_drop_below_minimum_until_made_private(env):
    pid = env.packs.create_pack(1, "Host", "Публичный", [f"слово{i}" for i in range(MIN_WORDS + 1)])
    env.packs.set_public(1, pid, True)
    words, _ = env.packs.list_words(1, pid, 0, 50)
    env.packs.delete_word(1, words[0][0])
    assert env.packs.info(2, pid).words_count == MIN_WORDS
    with pytest.raises(PackError, match="Сначала сделайте набор личным"):
        env.packs.delete_word(1, words[1][0])
    assert env.packs.info(2, pid).words_count == MIN_WORDS
    env.packs.set_public(1, pid, False)
    env.packs.delete_word(1, words[1][0])
    assert env.packs.info(1, pid).words_count == MIN_WORDS - 1
    with pytest.raises(PackError):
        env.packs.info(2, pid)


def test_migration_unpublishes_existing_invalid_user_packs(env):
    bad = env.packs.create_pack(1, "Host", "Было девять", [f"слово{i}" for i in range(9)])
    valid = env.packs.create_pack(1, "Host", "Корректный", [f"слово{i}" for i in range(10)])
    env.packs.set_public(1, valid, True)
    with env.packs._factory.begin() as db:
        db.get(QuestionPack, bad).is_public = True
    run_migrations(str(env.packs._factory.kw["bind"].url))
    assert not env.packs.info(1, bad).is_public
    assert env.packs.info(1, bad).words_count == 9
    assert env.packs.info(2, valid).is_public


def test_migration_converts_legacy_float_fields_and_recovers_exact_deadline(tmp_path):
    url = f"sqlite:///{tmp_path}/legacy-float.db"
    engine = create_engine(url)
    exact = 1_800_000_065.25
    rounded = struct.unpack("f", struct.pack("f", exact))[0]
    updated = datetime(2026, 11, 1, 12)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE rooms (id INTEGER PRIMARY KEY, code VARCHAR(8), invite_token VARCHAR(32), host_id BIGINT, state TEXT, deadline FLOAT, created_at DATETIME, updated_at DATETIME)"))
        conn.execute(text("CREATE TABLE user_penalties (user_id BIGINT PRIMARY KEY, strikes INTEGER, banned_until FLOAT NOT NULL, last_leave_at FLOAT NOT NULL, reason VARCHAR(16))"))
        conn.execute(text("INSERT INTO rooms (id,code,invite_token,host_id,state,deadline,created_at,updated_at) VALUES (1,'ABCDEF','abc',1,:state,:deadline,:updated,:updated)").bindparams(bindparam("updated", type_=DateTime())), {"state": json.dumps({"deadline": exact}), "deadline": rounded, "updated": updated})
        conn.execute(text("INSERT INTO user_penalties VALUES (1,1,:deadline,:deadline,'leave')"), {"deadline": rounded})
    run_migrations(url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT deadline FROM rooms WHERE id=1")).scalar_one() == exact
        activity = conn.execute(text("SELECT last_activity_at FROM rooms WHERE id=1")).scalar_one()
        assert str(activity).startswith("2026-11-01 12:00:00")
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0010"
    for table, names in [("rooms", ["deadline"]), ("user_penalties", ["banned_until", "last_leave_at"])]:
        cols = {c["name"]: c for c in inspect(engine).get_columns(table)}
        assert all("DOUBLE" in str(cols[n]["type"]).upper() for n in names)
    assert not next(c for c in inspect(engine).get_columns("rooms") if c["name"] == "last_activity_at")["nullable"]
    run_migrations(url)
    engine.dispose()
