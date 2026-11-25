import random

import pytest

from app.models.game.engine import GameError, Phase, Round
from app.models.game.roles import build_turn_order, choose_spy
from app.models.game.scoring import Outcome, ScoreRules
from app.models.game.voting import majority_reached


def make_round(spy="d", open_guess=False):
    r = Round(
        players=["a", "b", "c", "d"],
        spy_id=spy,
        word="кот",
        theme="t",
        allowed_words=["кот", "пёс", "лиса"],
        order=["a", "b", "c", "d"],
        open_guess=open_guess,
    )
    r.finish_deal()
    return r


def circle(r, wants):
    events = []
    for player, want in zip(list(r.order), wants):
        events.append(r.end_turn(player, want))
    return events


def start_voting(r):
    assert circle(r, [True, True, True, False])[-1] == "vote"
    assert r.phase == Phase.SUSPECT_VOTE


def catch_spy(r):
    start_voting(r)
    for p in ["a", "b", "c"]:
        r.cast_vote(p, "d")
    r.cast_vote("d", "a")


def test_majority_is_strict():
    assert not majority_reached(2, 4)
    assert majority_reached(3, 4)
    assert majority_reached(2, 3)


def test_turn_order_enforced():
    r = make_round()
    assert r.current() == "a" and r.next_player() == "b"
    with pytest.raises(GameError):
        r.end_turn("b")
    assert r.end_turn("a") == "next"
    assert r.current() == "b"


def test_voting_starts_only_after_full_circle():
    r = make_round()
    assert r.end_turn("a", True) == "next"
    assert r.end_turn("b", True) == "next"
    assert r.end_turn("c", True) == "next"
    assert r.phase == Phase.DISCUSSION
    assert r.end_turn("d", False) == "vote"
    assert r.phase == Phase.SUSPECT_VOTE


def test_no_majority_starts_new_circle():
    r = make_round()
    events = circle(r, [True, True, False, False])
    assert events[-1] == "new_circle"
    assert r.phase == Phase.DISCUSSION and r.circle_no == 2
    assert r.circle_votes == {} and r.current() == "a"


def test_exactly_half_is_not_majority():
    r = make_round()
    assert circle(r, [True, True, False, False])[-1] == "new_circle"


def test_end_turn_outside_discussion_rejected():
    r = make_round()
    start_voting(r)
    with pytest.raises(GameError):
        r.end_turn("a")


def test_vote_before_phase_rejected():
    r = make_round()
    with pytest.raises(GameError):
        r.cast_vote("a", "b")


def test_self_vote_rejected():
    r = make_round()
    start_voting(r)
    with pytest.raises(GameError):
        r.cast_vote("a", "a")


def test_revote_overwrites_not_duplicates():
    r = make_round()
    start_voting(r)
    r.cast_vote("a", "b")
    r.cast_vote("a", "c")
    assert r.votes == {"a": "c"}


def test_spy_caught_ends_round_at_once_civilians_win():
    r = make_round()
    catch_spy(r)
    assert r.phase == Phase.FINISHED and r.outcome == Outcome.CIVILIANS_WIN
    assert r.settle(ScoreRules()) == {"a": 1, "b": 1, "c": 1, "d": 0}


def test_wrong_accusation_spy_wins_three():
    r = make_round()
    start_voting(r)
    r.cast_vote("a", "b")
    r.cast_vote("c", "b")
    r.cast_vote("d", "b")
    r.cast_vote("b", "a")
    assert r.outcome == Outcome.SPY_WIN_VOTE
    assert r.settle(ScoreRules())["d"] == 3


def test_tie_runoff_then_tie_spy_wins():
    r = make_round()
    start_voting(r)
    for voter, target in [("a", "b"), ("b", "a"), ("c", "a"), ("d", "b")]:
        r.cast_vote(voter, target)
    assert r.phase == Phase.RUNOFF_VOTE
    assert r.candidates == ["a", "b"] and r.votes == {}
    for voter, target in [("a", "b"), ("b", "a"), ("c", "a"), ("d", "b")]:
        r.cast_vote(voter, target)
    assert r.outcome == Outcome.SPY_WIN_VOTE


def test_runoff_target_restricted_to_candidates():
    r = make_round()
    start_voting(r)
    for voter, target in [("a", "b"), ("b", "a"), ("c", "a"), ("d", "b")]:
        r.cast_vote(voter, target)
    with pytest.raises(GameError):
        r.cast_vote("c", "d")


def test_secret_correct_guess_wins_when_voting_would_start():
    r = make_round()
    r.secret_guess("d", "Кот ")
    assert r.phase == Phase.DISCUSSION and r.outcome is None
    assert r.secret_guess_correct is True
    events = circle(r, [True, True, True, True])
    assert events[-1] == "spy_won"
    assert r.outcome == Outcome.SPY_WIN_GUESS and r.phase == Phase.FINISHED
    assert r.settle(ScoreRules())["d"] == 3


def test_secret_correct_guess_does_nothing_until_vote_majority():
    r = make_round()
    r.secret_guess("d", "кот")
    assert circle(r, [False, False, False, False])[-1] == "new_circle"
    assert r.phase == Phase.DISCUSSION
    assert circle(r, [True, True, True, False])[-1] == "spy_won"
    assert r.outcome == Outcome.SPY_WIN_GUESS


def test_local_secret_wrong_guess_loses_when_voting_would_start():
    r = make_round()
    r.secret_guess("d", "пёс")
    assert r.secret_guess_correct is False and r.guesses_left == 0
    assert r.phase == Phase.DISCUSSION
    events = circle(r, [True, True, True, False])
    assert events[-1] == "spy_lost"
    assert r.phase == Phase.FINISHED and r.outcome == Outcome.SPY_WRONG_GUESS
    assert r.settle(ScoreRules()) == {"a": 1, "b": 1, "c": 1, "d": 0}


def test_local_secret_wrong_guess_waits_while_nobody_wants_voting():
    r = make_round()
    r.secret_guess("d", "пёс")
    assert circle(r, [False, False, True, False])[-1] == "new_circle"
    assert r.phase == Phase.DISCUSSION and r.outcome is None


def test_secret_guess_ignored_for_non_spy_and_invalid_and_repeat():
    r = make_round()
    before = r.to_dict()
    r.secret_guess("a", "кот")
    r.secret_guess("d", "самолёт")
    assert r.to_dict() == before
    r.secret_guess("d", "пёс")
    r.secret_guess("d", "кот")
    assert r.secret_guess_word == "пёс" and r.secret_guess_correct is False


def test_local_secret_guess_during_voting_is_recorded_and_draws_after_votes():
    r = make_round()
    start_voting(r)
    r.secret_guess("d", "кот")
    assert r.secret_guess_correct is True and r.phase == Phase.SUSPECT_VOTE
    for p in ["a", "b", "c"]:
        r.cast_vote(p, "d")
    r.cast_vote("d", "a")
    assert r.outcome == Outcome.DRAW
    assert set(r.settle(ScoreRules()).values()) == {0}


def test_local_secret_correct_guess_during_voting_wins_if_spy_not_found():
    r = make_round()
    start_voting(r)
    r.secret_guess("d", "кот")
    for voter, target in [("a", "b"), ("c", "b"), ("d", "b"), ("b", "a")]:
        r.cast_vote(voter, target)
    assert r.outcome == Outcome.SPY_WIN_GUESS


def test_local_secret_wrong_guess_during_voting_changes_nothing():
    r = make_round()
    start_voting(r)
    r.secret_guess("d", "пёс")
    for p in ["a", "b", "c"]:
        r.cast_vote(p, "d")
    r.cast_vote("d", "a")
    assert r.outcome == Outcome.CIVILIANS_WIN


def test_local_secret_correct_guess_wins_after_runoff_tie():
    r = make_round()
    start_voting(r)
    r.secret_guess("d", "кот")
    plan = [("a", "b"), ("b", "a"), ("c", "a"), ("d", "b")]
    for voter, target in plan:
        r.cast_vote(voter, target)
    assert r.phase == Phase.RUNOFF_VOTE and r.outcome is None
    for voter, target in plan:
        r.cast_vote(voter, target)
    assert r.outcome == Outcome.SPY_WIN_GUESS


def test_open_guess_not_allowed_during_discussion():
    r = make_round()
    with pytest.raises(GameError):
        r.guess("d", "кот")


def test_open_guess_before_voting_correct_wins_wrong_loses():
    win = make_round(open_guess=True)
    assert win.guess("d", "кот") is True
    assert win.outcome == Outcome.SPY_WIN_GUESS and win.settle(ScoreRules())["d"] == 3
    lose = make_round(open_guess=True)
    assert lose.guess("d", "пёс") is False
    assert lose.outcome == Outcome.SPY_WRONG_GUESS
    assert lose.settle(ScoreRules()) == {"a": 1, "b": 1, "c": 1, "d": 0}


def test_guess_during_voting_waits_for_votes_and_draws_if_spy_found():
    for open_guess in (False, True):
        r = make_round(open_guess=open_guess)
        start_voting(r)
        assert r.guess("d", "кот") is True
        assert r.phase == Phase.SUSPECT_VOTE and r.outcome is None
        for voter in ["a", "b", "c", "d"]:
            r.cast_vote(voter, "d" if voter != "d" else "a")
        assert r.outcome == Outcome.DRAW
        assert set(r.settle(ScoreRules()).values()) == {0}


def test_no_last_chance_phase_is_ever_entered():
    r = make_round()
    catch_spy(r)
    assert r.phase == Phase.FINISHED


def test_wrong_guess_during_voting_keeps_round_running():
    r = make_round()
    start_voting(r)
    assert r.guess("d", "пёс") is False
    assert r.phase == Phase.SUSPECT_VOTE
    with pytest.raises(GameError):
        r.guess("d", "кот")


def test_non_spy_cannot_guess_openly_and_unknown_word_rejected():
    r = make_round()
    start_voting(r)
    with pytest.raises(GameError):
        r.guess("a", "кот")
    with pytest.raises(GameError):
        r.guess("d", "самолёт")


def test_round_cannot_finish_twice():
    r = make_round()
    start_voting(r)
    r.guess("d", "кот")
    for voter in ["a", "b", "c", "d"]:
        r.cast_vote(voter, "d" if voter != "d" else "a")
    with pytest.raises(GameError):
        r.guess("d", "кот")
    with pytest.raises(GameError):
        r.cast_vote("a", "b")
    with pytest.raises(GameError):
        r.end_turn("a")


def test_settle_is_idempotent():
    r = make_round()
    start_voting(r)
    r.guess("d", "кот")
    for voter in ["a", "b", "c", "d"]:
        r.cast_vote(voter, "d" if voter != "d" else "a")
    r.settle(ScoreRules())
    with pytest.raises(GameError):
        r.settle(ScoreRules())


def test_roundtrip_serialization_and_legacy_keys():
    r = make_round()
    r.secret_guess("d", "пёс")
    r.end_turn("a", True)
    data = r.to_dict()
    assert Round.from_dict(data).to_dict() == data
    legacy = dict(data)
    legacy["vote_requests"] = ["a"]
    del legacy["circle_no"]
    assert Round.from_dict(legacy).circle_no == 1


def test_spy_never_first_speaker():
    players = ["p0", "p1", "p2", "p3", "p4"]
    for seed in range(500):
        rng = random.Random(seed)
        spy = rng.choice(players)
        assert build_turn_order(players, spy, rng)[0] != spy


def test_spy_not_repeated_when_possible():
    players = ["p0", "p1", "p2"]
    for seed in range(200):
        assert choose_spy(players, "p1", random.Random(seed)) != "p1"
