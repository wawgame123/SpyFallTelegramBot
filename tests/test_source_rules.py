from tests.pack_fixtures import test_library
import random

import pytest

from app.models.database.session import make_session_factory
from app.models.game.engine import GameError, Phase, Round
from app.models.game.room import RoomError
from app.models.game.scoring import Outcome, ScoreRules
from app.models.game.session import LocalSession, Stage
from app.models.services.room_service import RoomService
from tests.helpers import local_circle, local_guess, room_circle, room_guess


def make_round(open_guess=True):
    r = Round(
        players=["a", "b", "c", "d"],
        spy_id="d",
        word="кот",
        theme="t",
        allowed_words=["кот", "пёс", "лиса"],
        order=["a", "b", "c", "d"],
        open_guess=open_guess,
    )
    r.finish_deal()
    return r


def to_vote(r):
    for p in r.order:
        r.end_turn(p, True)
    assert r.phase == Phase.SUSPECT_VOTE


def vote_for(r, target_for):
    for p in r.players:
        r.cast_vote(p, target_for(p))


def test_guess_outside_voting_correct_spy_wins_three():
    r = make_round()
    assert r.guess("d", "кот") is True
    assert r.outcome == Outcome.SPY_WIN_GUESS
    assert r.settle(ScoreRules()) == {"a": 0, "b": 0, "c": 0, "d": 3}


def test_guess_outside_voting_wrong_civilians_win_at_once():
    r = make_round()
    assert r.guess("d", "пёс") is False
    assert r.phase == Phase.FINISHED and r.outcome == Outcome.SPY_WRONG_GUESS
    assert r.settle(ScoreRules()) == {"a": 1, "b": 1, "c": 1, "d": 0}


def test_guess_during_voting_correct_is_draw():
    r = make_round()
    to_vote(r)
    assert r.guess("d", "кот") is True
    assert r.outcome is None
    vote_for(r, lambda voter: "d" if voter != "d" else "a")
    assert r.outcome == Outcome.DRAW
    assert set(r.settle(ScoreRules()).values()) == {0}


def test_guess_during_voting_wrong_burns_attempt_and_vote_decides():
    found = make_round()
    to_vote(found)
    assert found.guess("d", "пёс") is False
    assert found.phase == Phase.SUSPECT_VOTE and found.guesses_left == 0
    with pytest.raises(GameError):
        found.guess("d", "кот")
    vote_for(found, lambda p: "d" if p != "d" else "a")
    assert found.outcome == Outcome.CIVILIANS_WIN
    lost = make_round()
    to_vote(lost)
    lost.guess("d", "пёс")
    vote_for(lost, lambda p: "b" if p != "b" else "a")
    assert lost.outcome == Outcome.SPY_WIN_VOTE


def test_spy_found_after_voting_ends_game_immediately_for_civilians():
    r = make_round()
    to_vote(r)
    vote_for(r, lambda p: "d" if p != "d" else "a")
    assert r.phase == Phase.FINISHED and r.outcome == Outcome.CIVILIANS_WIN
    assert r.settle(ScoreRules()) == {"a": 1, "b": 1, "c": 1, "d": 0}


def test_players_failed_after_voting_lose_immediately_spy_wins_three():
    r = make_round()
    to_vote(r)
    vote_for(r, lambda p: "b" if p != "b" else "a")
    assert r.phase == Phase.FINISHED and r.outcome == Outcome.SPY_WIN_VOTE
    assert r.settle(ScoreRules())["d"] == 3
    tie = make_round()
    to_vote(tie)
    plan = {"a": "b", "b": "a", "c": "a", "d": "b"}
    vote_for(tie, lambda p: plan[p])
    assert tie.phase == Phase.RUNOFF_VOTE
    vote_for(tie, lambda p: plan[p])
    assert tie.outcome == Outcome.SPY_WIN_VOTE and tie.settle(ScoreRules())["d"] == 3


def test_scoring_idempotent_and_round_finishes_once():
    r = make_round()
    r.guess("d", "кот")
    r.settle(ScoreRules())
    with pytest.raises(GameError):
        r.settle(ScoreRules())
    with pytest.raises(GameError):
        r.guess("d", "кот")


def make_service(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path}/src.db")
    return RoomService(factory, rng=random.Random(4))


def make_room(service, first=1, n=4):
    upd = service.create(first, "Хост")
    ids = list(range(first, first + n))
    for uid in ids[1:]:
        service.join_code(uid, f"И{uid}", upd.room.code)
    service.act(first, "ui:settings")
    service.act(first, "th:animals")
    for uid in ids[1:]:
        service.act(uid, "rd:1")
    service.act(first, "start")
    for uid in ids:
        service.act(uid, "role")
    return upd.room_id


def vote_everyone(service, rid, spy_target):
    room = service.get(rid)
    spy = int(room.round.spy_id)
    other = next(p["id"] for p in room.players if p["id"] != spy)
    for player in room.players:
        uid = player["id"]
        if spy_target:
            target = spy if uid != spy else other
        else:
            target = other if uid != other else spy
        service.act(uid, f"v:{target}")


def test_online_guess_before_voting_correct_wins_wrong_loses(tmp_path):
    service = make_service(tmp_path)
    rid = make_room(service)
    spy = int(service.get(rid).round.spy_id)
    room_guess(service, rid, spy, correct=True)
    won = service.get(rid)
    assert won.round.outcome == Outcome.SPY_WIN_GUESS and won.scores[str(spy)] == 3
    rid2 = make_room(service, first=11)
    spy2 = int(service.get(rid2).round.spy_id)
    room_guess(service, rid2, spy2, correct=False)
    lost = service.get(rid2)
    assert lost.round.outcome == Outcome.SPY_WRONG_GUESS and lost.scores[str(spy2)] == 0
    assert sum(lost.scores.values()) == 3


@pytest.mark.parametrize("spy_found", [True, False])
def test_online_correct_guess_during_voting_waits_for_votes(tmp_path, spy_found):
    service = make_service(tmp_path)
    rid = make_room(service)
    spy = int(service.get(rid).round.spy_id)
    room_circle(service, rid, True)
    room_guess(service, rid, spy, correct=True)
    assert service.get(rid).round.outcome is None
    vote_everyone(service, rid, spy_target=spy_found)
    done = service.get(rid)
    assert done.round.outcome == (Outcome.DRAW if spy_found else Outcome.SPY_WIN_GUESS)
    assert done.scores[str(spy)] == (0 if spy_found else 3)


def test_online_voting_result_ends_game_immediately(tmp_path):
    service = make_service(tmp_path)
    rid = make_room(service)
    room_circle(service, rid, True)
    vote_everyone(service, rid, spy_target=True)
    assert service.get(rid).round.outcome == Outcome.CIVILIANS_WIN
    rid2 = make_room(service, first=11)
    room_circle(service, rid2, True)
    vote_everyone(service, rid2, spy_target=False)
    done = service.get(rid2)
    spy2 = int(done.round.spy_id)
    assert done.round.outcome == Outcome.SPY_WIN_VOTE and done.scores[str(spy2)] == 3


def test_online_wrong_guess_during_voting_is_private(tmp_path):
    service = make_service(tmp_path)
    rid = make_room(service)
    room = service.get(rid)
    spy = int(room.round.spy_id)
    others = [p["id"] for p in room.players if p["id"] != spy]
    room_circle(service, rid, True)
    room = service.get(rid)
    before = {uid: room.view_for(uid, "bot", 0).text for uid in others}
    room_guess(service, rid, spy, correct=False)
    after = service.get(rid)
    assert after.phase == "playing"
    for uid in others:
        assert after.view_for(uid, "bot", 0).text == before[uid]


def test_online_only_spy_can_guess(tmp_path):
    service = make_service(tmp_path)
    rid = make_room(service)
    room = service.get(rid)
    other = next(p["id"] for p in room.players if p["id"] != int(room.round.spy_id))
    with pytest.raises(RoomError):
        service.act(other, "gu")


def local_session(seed=3):
    s = LocalSession(rng=random.Random(seed), library=test_library())
    s.act(s.seq, "n:4")
    for name in ["А", "Б", "В", "Г"]:
        s.handle_text(name)
    s.act(s.seq, "th")
    s.act(s.seq, "t:animals")
    s.act(s.seq, "ok")
    s.act(s.seq, "go")
    for _ in range(4):
        s.act(s.seq, "me")
        s.act(s.seq, "show")
        s.act(s.seq, "next")
    s.act(s.seq, "start")
    return s


def local_vote(s, spy_found):
    spy = s.round.spy_id
    other = next(p for p in s.pids if p != spy)
    for _ in range(4):
        voter = s.round.players[s.vote_idx]
        if spy_found:
            target = spy if voter != spy else other
        else:
            target = other if voter != other else spy
        s.act(s.seq, "me")
        s.act(s.seq, f"v:{target}")
        if s.stage == Stage.RESULT:
            break


@pytest.mark.parametrize("correct", [True, False])
def test_local_last_chance_decides_between_draw_and_civilians_win(correct):
    s = local_session(seed=6)
    local_circle(s, True)
    local_vote(s, True)
    assert s.stage == Stage.GUESS_HANDOFF and s.round.outcome is None
    local_guess(s, correct=correct)
    assert s.stage == Stage.RESULT
    assert s.round.outcome == (Outcome.DRAW if correct else Outcome.CIVILIANS_WIN)
    assert s.scores[s.round.spy_id] == 0
    assert sum(s.scores.values()) == (0 if correct else len(s.pids) - 1)


def test_local_caught_spy_can_skip_and_uncaught_spy_wins_immediately():
    found = local_session(seed=7)
    local_circle(found, True)
    local_vote(found, True)
    assert found.stage == Stage.GUESS_HANDOFF
    found.act(found.seq, "skip")
    assert found.stage == Stage.RESULT and found.round.outcome == Outcome.CIVILIANS_WIN
    not_found = local_session(seed=8)
    local_circle(not_found, True)
    local_vote(not_found, False)
    assert not_found.stage == Stage.RESULT and not_found.round.outcome == Outcome.SPY_WIN_VOTE
