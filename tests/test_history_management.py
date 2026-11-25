import pytest
from sqlalchemy import select, inspect
from aiogram.methods import EditMessageText

from tests.test_bot_flow import Env, run
from app.models.database.models import GameSession, MatchRound, RoundPlayer, SessionPlayer, QuestionPack
from app.models.services.history_admin_service import HistoryAdminService
from app.models.services.admin_service import AdminError, AdminService
from app.models.services.history_service import HistoryService
from app.models.services.pack_service import PackError
from app.models.database.migrate import run_migrations


@pytest.fixture
def setup(tmp_path):
    env = Env(tmp_path)
    factory = env.packs._factory
    pid = env.packs.create_pack(2, "Author", "Исторический пак", [f"слово{i}" for i in range(10)])
    with factory.begin() as db:
        session = GameSession(room_code="ABCDEF", title="Тест", host_id=1, status="finished")
        db.add(session); db.flush()
        match = MatchRound(session_id=session.id, round_no=1, theme="Тема", word="слово1", spy_user_id=2, spy_name="Игрок2", outcome="spy_win_guess", pack_id=pid, pack_title="Исторический пак")
        db.add(match); db.flush()
        rid, sid = match.id, session.id
        for uid in (1, 2):
            db.add(SessionPlayer(session_id=sid, user_id=uid, name=f"Игрок{uid}", score=uid))
            db.add(RoundPlayer(round_id=rid, user_id=uid, name=f"Игрок{uid}", role="spy" if uid == 2 else "civilian", result="win" if uid == 2 else "loss", score_delta=3 if uid == 2 else 0))
    return env, factory, pid, rid, sid


def buttons(env):
    return [b.callback_data for row in env.session.of(EditMessageText)[-1].reply_markup.inline_keyboard for b in row]


def test_history_lists_round_pack_and_clickable_players(setup):
    env, factory, pid, rid, sid = setup
    async def scenario():
        await env.press(1, "hist:games:0")
        assert "Набор: Исторический пак" in env.last_text()
        assert f"hist:rd:{rid}" in buttons(env)
        await env.press(1, f"hist:rd:{rid}")
        assert f"hist:player:{rid}:2" in buttons(env)
        assert f"hist:pack:{rid}" in buttons(env)
        assert "hist:games:0" in buttons(env)
        await env.press(1, f"hist:rd:{rid}:l:2:3")
        assert f"hist:lr:{sid}:2:3" in buttons(env)
        await env.press(1, f"hist:pack:{rid}")
        await env.press(1, f"hist:rd:{rid}")
        assert f"hist:lr:{sid}:2:3" in buttons(env)
        await env.press(1, f"hist:sp:{sid}:2")
        assert "Telegram ID: 2" in env.last_text()
    run(scenario())


def test_non_admin_can_view_id_but_cannot_moderate(setup):
    env, factory, pid, rid, sid = setup
    async def scenario():
        await env.press(2, f"hist:player:{rid}:1")
        assert "Telegram ID: 1" in env.last_text()
        assert not any(b.startswith("hist:admin:") for b in buttons(env))
        await env.press(2, f"hist:admin:hide:{rid}")
        assert not HistoryAdminService(factory).pack(pid)["hidden"]
        await env.press(2, f"hist:admin:banok:{rid}:1:perm")
        assert AdminService(factory).user_info(1)["ban_seconds"] == 0
    run(scenario())


def test_forged_foreign_context_is_denied(setup):
    env, factory, pid, rid, sid = setup
    async def scenario():
        await env.press(3, f"hist:rd:{rid}")
        from dataclasses import replace
        original_settings = env.dp["settings"]
        env.dp["settings"] = replace(original_settings, admin_id=3)
        await env.press(3, f"hist:admin:deleteok:{rid}", username="WAWGAME")
        env.dp["settings"] = original_settings
        assert HistoryAdminService(factory).pack(pid)["id"] == pid
        await env.press(1, f"hist:admin:banok:{rid}:999:perm", username="WAWGAME")
        assert AdminService(factory).user_info(999)["ban_seconds"] == 0
    run(scenario())


def test_ban_requires_confirmation_and_self_is_protected(setup):
    env, factory, pid, rid, sid = setup
    async def scenario():
        await env.press(1, f"hist:admin:ban:{rid}:2:60", username="WAWGAME")
        assert AdminService(factory).user_info(2)["ban_seconds"] == 0
        await env.press(1, f"hist:admin:banok:{rid}:2:60", username="WAWGAME")
        assert AdminService(factory).user_info(2)["ban_seconds"] > 3500
        await env.press(1, f"hist:admin:unban:{rid}:2", username="WAWGAME")
        assert AdminService(factory).user_info(2)["ban_seconds"] == 0
        await env.press(1, f"hist:admin:banok:{rid}:1:perm", username="WAWGAME")
        assert AdminService(factory).user_info(1)["ban_seconds"] == 0
    run(scenario())


def test_pack_controls_and_title_snapshot(setup):
    env, factory, pid, rid, sid = setup
    async def scenario():
        for action, expected in [("public", True), ("private", False)]:
            await env.press(1, f"hist:admin:{action}:{rid}", username="WAWGAME")
            assert HistoryAdminService(factory).pack(pid)["public"] == expected
        await env.press(1, f"hist:admin:hide:{rid}", username="WAWGAME")
        assert HistoryAdminService(factory).pack(pid)["hidden"]
        await env.press(1, f"hist:admin:public:{rid}", username="WAWGAME")
        assert not HistoryAdminService(factory).pack(pid)["public"]
        await env.press(1, f"hist:admin:restore:{rid}", username="WAWGAME")
        await env.press(1, f"hist:admin:rename:{rid}", username="WAWGAME")
        await env.text(1, "Новое имя", username="WAWGAME")
        assert HistoryAdminService(factory).pack(pid)["title"] == "Новое имя"
        assert HistoryService(factory).round_detail(1, rid)["pack_title"] == "Исторический пак"
        await env.press(1, f"hist:admin:words:{rid}", username="WAWGAME")
        await env.text(1, "кот, лиса, пёс", username="WAWGAME")
        assert HistoryAdminService(factory).pack(pid)["words"] == ["кот", "лиса", "пёс"]
        await env.press(1, f"hist:admin:public:{rid}", username="WAWGAME")
        assert not HistoryAdminService(factory).pack(pid)["public"]
    run(scenario())


def test_cancel_rename_and_delete_confirmation(setup):
    env, factory, pid, rid, sid = setup
    async def scenario():
        await env.press(1, f"hist:admin:rename:{rid}", username="WAWGAME")
        await env.press(1, f"hist:pack:{rid}", username="WAWGAME")
        await env.text(1, "Не название", username="WAWGAME")
        assert HistoryAdminService(factory).pack(pid)["title"] == "Исторический пак"
        await env.press(1, f"hist:admin:delete:{rid}", username="WAWGAME")
        assert HistoryAdminService(factory).pack(pid)["id"] == pid
        await env.press(1, f"hist:admin:deleteok:{rid}", username="WAWGAME")
        with pytest.raises(AdminError):
            HistoryAdminService(factory).pack(pid)
        detail = HistoryService(factory).round_detail(1, rid)
        assert detail["pack_id"] == pid and detail["pack_title"] == "Исторический пак"
        await env.press(1, f"hist:pack:{rid}", username="WAWGAME")
        assert "удалён" in env.last_text()
    run(scenario())


def test_old_round_does_not_guess_pack_by_title(setup):
    env, factory, pid, rid, sid = setup
    with factory.begin() as db:
        db.get(MatchRound, rid).pack_id = None
    async def scenario():
        await env.press(1, f"hist:pack:{rid}", username="WAWGAME")
        assert "точный ID" in env.last_text()
        await env.press(1, f"hist:admin:hide:{rid}", username="WAWGAME")
        assert not HistoryAdminService(factory).pack(pid)["hidden"]
    run(scenario())


def test_edit_input_precedes_in_room_chat(setup):
    env, factory, pid, rid, sid = setup
    async def scenario():
        await env.press(1, "online:create", message_id=700)
        await env.press(1, f"hist:admin:rename:{rid}", username="WAWGAME")
        await env.text(1, "Название из истории", username="WAWGAME")
        assert HistoryAdminService(factory).pack(pid)["title"] == "Название из истории"
    run(scenario())


def test_builtin_admin_changes_apply_to_library_without_restart(setup):
    env, factory, pid, rid, sid = setup
    with factory() as db:
        builtin = db.execute(select(QuestionPack).where(QuestionPack.owner_id.is_(None))).scalars().first()
        bid, slug = builtin.id, builtin.slug
    manager = HistoryAdminService(factory)
    manager.update(bid, "rename", "Новое встроенное имя")
    assert env.packs.library_for(1).get(slug).title == "Новое встроенное имя"
    manager.update(bid, "hide")
    assert not env.packs.library_for(1).has(slug)
    manager.update(bid, "restore")
    manager.update(bid, "public")
    assert env.packs.library_for(1).has(slug)


def test_history_snapshot_records_exact_pack(setup):
    env, factory, pid, rid, sid = setup
    from tests.test_history import new_room, play_round
    room_id = new_room(env.service, n=3, host=10)
    room = play_round(env.service, room_id, host=10)
    with factory() as db:
        match = db.execute(select(MatchRound).where(MatchRound.session_id == room.session_id)).scalar_one()
        pack = db.get(QuestionPack, match.pack_id)
        assert pack.slug == room.round.theme_id
        assert match.pack_title == pack.title


def test_migration_adds_pack_snapshot_columns(tmp_path):
    from sqlalchemy import create_engine
    url = f"sqlite:///{tmp_path}/migration.db"
    run_migrations(url)
    engine = create_engine(url)
    assert {"pack_id", "pack_title"} <= {c["name"] for c in inspect(engine).get_columns("match_rounds")}
    with engine.connect() as conn:
        from sqlalchemy import text
        assert conn.execute(text("select version_num from alembic_version")).scalar_one() == "0014"
    engine.dispose()


def test_hidden_pack_cannot_start_new_round_and_active_pack_cannot_be_deleted(setup):
    from app.models.game.room import RoomError
    env, factory, pid, rid, sid = setup
    manager = HistoryAdminService(factory)
    with factory() as db:
        slug = db.get(QuestionPack, pid).slug
    update = env.service.create(2, "Author")
    for uid in (3, 4):
        env.service.join_code(uid, f"Player{uid}", update.room.code)
    env.service.act(2, "ui:settings")
    env.service.act(2, f"th:{slug}")
    for uid in (3, 4):
        env.service.act(uid, "rd:1")
    with pytest.raises(AdminError, match="действующей комнате"):
        manager.update(pid, "delete")
    manager.update(pid, "hide")
    with pytest.raises(RoomError, match="недоступен"):
        env.service.act(2, "start")


def test_migration_recovers_only_exact_snapshot(setup):
    import json
    from app.models.database.models import RoomRecord
    env, factory, pid, rid, sid = setup
    with factory.begin() as db:
        match = db.get(MatchRound, rid)
        match.pack_id = None
        match.pack_title = None
        slug = db.get(QuestionPack, pid).slug
        db.add(RoomRecord(code="ZZZZZZ", invite_token="test-recovery", host_id=1, state=json.dumps({"session_id": sid, "round_no": 1, "round": {"theme_id": slug, "word": match.word}})))
    run_migrations(str(factory.kw["bind"].url))
    detail = HistoryService(factory).round_detail(1, rid)
    assert detail["pack_id"] == pid
    assert detail["pack_title"] == "Тема"


def test_pack_snapshot_keeps_name_from_round_start(setup):
    env, factory, pid, rid, sid = setup
    from tests.test_history import new_room
    from tests.helpers import room_guess
    room_id = new_room(env.service, n=3, host=20)
    env.service.act(20, "start")
    room = env.service.get(room_id)
    original_title = room.round.theme
    with factory() as db:
        pack = db.execute(select(QuestionPack).where(QuestionPack.slug == room.round.theme_id)).scalar_one()
        bid = pack.id
    HistoryAdminService(factory).update(bid, "rename", "Название после старта")
    for p in room.players:
        env.service.act(p["id"], "role")
    room_guess(env.service, room_id, int(room.round.spy_id), correct=True)
    room = env.service.get(room_id)
    with factory() as db:
        match = db.execute(select(MatchRound).where(MatchRound.session_id == room.session_id)).scalar_one()
        assert match.pack_title == original_title
        assert match.pack_id == bid
