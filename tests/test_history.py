from tests.pack_fixtures import DEFAULT_PACKS
import pathlib
import random
import re

import pytest
from sqlalchemy import func, select

from app.bot import history_view as view
from app.bot.keyboards.menu import (
    ONLINE_TEXT,
    RULES_TEXT,
    admin_lobby_menu,
    local_choice_menu,
    online_menu,
)
from app.database.models import GameSession, MatchRound, RoundPlayer
from app.database.pack_repository import PackRepository
from app.database.session import make_session_factory
from app.game.room import RoomError
from app.game.session import LocalSession
from app.services.history_service import HistoryService
from app.services.room_service import RoomService
from tests.helpers import room_circle, room_guess

EMOJI = re.compile(
    "[\U0001F000-\U0001FFFF\u2190-\u21FF\u2300-\u23FF\u25A0-\u25FF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]"
)


@pytest.fixture
def env(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path}/h.db")
    return factory, RoomService(factory, rng=random.Random(3)), HistoryService(factory)


def new_room(service, n=4, host=1):
    upd = service.create(host, "Хост")
    for uid in range(host + 1, host + n):
        service.join_code(uid, f"Игрок{uid}", upd.room.code)
    service.act(host, "ui:settings")
    service.act(host, "th:animals")
    for uid in range(host + 1, host + n):
        service.act(uid, "rd:1")
    return upd.room_id


def play_round(service, rid, host=1, outcome="spy_guess"):
    if service.get(rid).phase in ("lobby", "result"):
        service.act(host, "start" if service.get(rid).phase == "lobby" else "next")
    room = service.get(rid)
    for p in room.players:
        service.act(p["id"], "role")
    room = service.get(rid)
    spy = int(room.round.spy_id)
    words = sorted(room.round.allowed_words, key=str.casefold)
    if outcome == "spy_guess":
        room_guess(service, rid, spy, correct=True)
    else:
        other = next(p["id"] for p in room.players if p["id"] != spy)
        room_circle(service, rid, True)
        for p in room.players:
            uid = p["id"]
            service.act(uid, f"v:{spy if uid != spy else other}")
    return service.get(rid)


def test_round_is_recorded_with_players_and_results(env):
    factory, service, _ = env
    rid = new_room(service)
    room = play_round(service, rid, outcome="spy_guess")
    spy = int(room.round.spy_id)
    with factory() as db:
        match = db.execute(select(MatchRound)).scalar_one()
        assert match.word == room.round.word and match.spy_user_id == spy
        assert match.outcome == "spy_win_guess" and match.round_no == 1
        rows = db.execute(select(RoundPlayer)).scalars().all()
        assert len(rows) == 4
        by_user = {r.user_id: r for r in rows}
        assert by_user[spy].role == "spy" and by_user[spy].result == "win"
        assert by_user[spy].score_delta == 3
        others = [r for u, r in by_user.items() if u != spy]
        assert all(r.result == "loss" and r.score_delta == 0 for r in others)


def test_recording_is_idempotent_and_multiple_rounds(env):
    factory, service, _ = env
    rid = new_room(service)
    play_round(service, rid, outcome="spy_guess")
    service.act(1, "set")
    service.act(1, "ui:lobby")
    service.act(1, "start")
    for p in service.get(rid).players:
        service.act(p["id"], "role")
    with factory() as db:
        assert db.execute(select(func.count()).select_from(MatchRound)).scalar_one() == 1
        assert db.execute(select(func.count()).select_from(GameSession)).scalar_one() == 1


def test_civilians_win_and_draw_results(env):
    factory, service, history = env
    rid = new_room(service)
    room = play_round(service, rid, outcome="caught")
    spy = int(room.round.spy_id)
    with factory() as db:
        rows = {r.user_id: r for r in db.execute(select(RoundPlayer)).scalars()}
    assert rows[spy].result == "loss"
    assert all(r.result == "win" and r.score_delta == 1 for u, r in rows.items() if u != spy)


def test_user_rounds_history(env):
    _, service, history = env
    rid = new_room(service)
    play_round(service, rid, outcome="spy_guess")
    play_round(service, rid, outcome="caught")
    rows, total = history.user_rounds(2, 0, 5)
    assert total == 2 and len(rows) == 2
    assert rows[0].lobby_title == "Комната Хост"
    assert {r.role for r in rows} <= {"spy", "civilian"}
    assert history.user_rounds(999, 0, 5) == ([], 0)
    page2, total2 = history.user_rounds(2, 1, 1)
    assert total2 == 2 and len(page2) == 1


def test_lobby_history_winner_and_status(env):
    _, service, history = env
    rid = new_room(service)
    play_round(service, rid, outcome="spy_guess")
    lobbies, total = history.user_lobbies(3, 0, 5)
    assert total == 1
    lobby = lobbies[0]
    assert lobby.status == "active" and lobby.rounds_count == 1
    room = service.get(rid)
    assert sum(s for _, s in lobby.players) == 3
    assert lobby.winners == [room.name_of(room.round.spy_id)]
    service.act(1, "end")
    finished = history.lobby(3, lobby.id)
    assert finished.status == "finished" and finished.ended_at is not None


def test_lobby_without_rounds_not_in_history(env):
    _, service, history = env
    new_room(service)
    assert history.user_lobbies(1, 0, 5) == ([], 0)


def test_abandoned_when_everyone_leaves(env):
    _, service, history = env
    rid = new_room(service, n=3)
    play_round(service, rid, outcome="spy_guess")
    for uid in (1, 2, 3):
        service.act(uid, "leave")
    lobbies, _ = history.user_lobbies(2, 0, 5)
    assert lobbies[0].status == "abandoned"


def test_purge_marks_abandoned(env):
    _, service, history = env
    rid = new_room(service)
    play_round(service, rid, outcome="spy_guess")
    assert service.purge_stale(hours=-1) == 1
    lobbies, _ = history.user_lobbies(1, 0, 5)
    assert lobbies[0].status == "abandoned"


def test_history_access_control(env):
    _, service, history = env
    rid = new_room(service, n=3)
    play_round(service, rid, outcome="spy_guess")
    other = new_room(service, n=3, host=50)
    play_round(service, other, host=50, outcome="spy_guess")
    mine, _ = history.user_lobbies(1, 0, 5)
    theirs, _ = history.user_lobbies(50, 0, 5)
    assert history.lobby(1, theirs[0].id) is None
    assert history.lobby_rounds(1, theirs[0].id, 0, 5) is None
    assert history.lobby_rounds(1, mine[0].id, 0, 5) is not None
    assert history.lobby(1, 99999) is None
    assert history.lobby(1, mine[0].id) is not None


def test_lobby_rounds_pagination_and_scores(env):
    _, service, history = env
    rid = new_room(service)
    for _ in range(3):
        play_round(service, rid, outcome="caught")
    lobby_id = history.user_lobbies(1, 0, 5)[0][0].id
    rows, total = history.lobby_rounds(1, lobby_id, 0, 2)
    assert total == 3 and [r.round_no for r in rows] == [1, 2]
    rows2, _ = history.lobby_rounds(1, lobby_id, 1, 2)
    assert [r.round_no for r in rows2] == [3]
    assert all(len(r.scores) == 4 for r in rows)


def test_history_screens_content(env):
    _, service, history = env
    rid = new_room(service)
    room = play_round(service, rid, outcome="caught")
    word = room.round.word
    games, total = history.user_rounds(2, 0, view.PAGE_SIZE)
    text = view.games_screen(games, total, 0).text
    assert word in text and "победили обычные игроки" in text and games[0].spy_name in text
    lobbies, total = history.user_lobbies(2, 0, view.PAGE_SIZE)
    screen = view.lobbies_screen(lobbies, total, 0)
    assert any(action.startswith("hist:lobby:") for row in screen.rows for _, action in row)
    detail = view.lobby_screen(lobbies[0])
    assert "Победитель лобби" in detail.text and "Игроки и очки" in detail.text
    assert any(a.startswith("hist:lr:") for row in detail.rows for _, a in row)
    rounds, total = history.lobby_rounds(2, lobbies[0].id, 0, view.PAGE_SIZE)
    rs = view.lobby_rounds_screen(lobbies[0].id, rounds, total, 0)
    assert word in rs.text and "Очки:" in rs.text


def test_empty_history_screens():
    assert "ещё не сыграли" in view.games_screen([], 0, 0).text
    assert "ещё не играли" in view.lobbies_screen([], 0, 0).text


def test_base_keyboards_leave_optional_emoji_to_the_styling_layer():
    from app.bot.emoji import decorate_markup
    for markup in (online_menu(True), online_menu(False, True), admin_lobby_menu(), local_choice_menu()):
        for row in markup.inline_keyboard:
            for button in row:
                assert not EMOJI.search(button.text)
        assert decorate_markup(markup, emoji=False, colors=False) == markup
    decorated = decorate_markup(online_menu(False), emoji=True, colors=False)
    assert any(EMOJI.search(b.text) for row in decorated.inline_keyboard for b in row)


def test_generated_game_buttons_allow_plain_styling(env):
    _, service, _ = env
    rid = new_room(service)
    seen = []
    seen.append(service.get(rid).view_for(1, "bot", 0))
    service.act(1, "start")
    seen.append(service.get(rid).view_for(2, "bot", 0))
    for p in service.get(rid).players:
        service.act(p["id"], "role")
    room = service.get(rid)
    for p in room.players:
        seen.append(room.view_for(p["id"], "bot", 0))
    local = LocalSession(rng=random.Random(1))
    seen.append(local.screen())
    for screen in seen:
        assert all(not EMOJI.search(label) for row in screen.rows for label, _ in row)


def test_database_pack_titles_are_not_overwritten_automatically(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path}/p.db")
    repo = PackRepository(factory)
    repo.seed(DEFAULT_PACKS)
    from app.database.models import QuestionPack

    with factory.begin() as db:
        pack = db.execute(select(QuestionPack).where(QuestionPack.slug == "animals")).scalar_one()
        pack.title = "Старое название"
    assert repo.load_library().get("animals").title == "Старое название"
