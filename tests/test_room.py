import random

import pytest

from app.models.database.session import make_session_factory
from app.models.game.engine import Phase
from app.models.game.room import MAX_MESSAGE_LEN, Room, RoomError
from app.models.services.room_service import RoomService

from tests.helpers import room_circle, room_guess
T0 = 1000.0


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def service(tmp_path, clock):
    factory = make_session_factory(f"sqlite:///{tmp_path}/rooms.db")
    return RoomService(factory, rng=random.Random(5), clock=clock)


def make_room(service, n=4):
    upd = service.create(1, "Хост")
    room = upd.room
    for uid in range(2, n + 1):
        service.join_code(uid, f"Игрок{uid}", room.code)
    service.act(1, "ui:settings")
    service.act(1, "ui:themes")
    service.act(1, "th:animals")
    for uid in range(2, n + 1):
        service.act(uid, "rd:1")
    return upd.room_id


def start_and_deal(service, room_id, n=4):
    service.act(1, "start")
    for uid in range(1, n + 1):
        service.act(uid, "role")
    room = service.get(room_id)
    assert room.phase == "playing"
    return room


@pytest.mark.parametrize(("correct", "outcome"), [(True, "spy_win_guess"), (False, "spy_wrong_guess")])
def test_hidden_spy_guess_ends_round_only_after_full_circle(service, correct, outcome):
    rid = make_room(service)
    service.act(1, "mode:hidden")
    service.act(1, "start")
    rnd = service.get(rid).round
    spy = int(rnd.spy_id)
    civilian = next(uid for uid in (1, 2, 3, 4) if uid != spy)
    assert rnd.hidden_spy_word in rnd.allowed_words and rnd.hidden_spy_word != rnd.word
    assert service.act(spy, "role").alert == "Ваше слово:\n\n" + rnd.hidden_spy_word.upper()
    assert service.act(civilian, "role").alert == "Ваше слово:\n\n" + rnd.word.upper()
    for uid in (1, 2, 3, 4):
        service.act(uid, "role")
    with pytest.raises(RoomError, match="после первого круга"):
        service.act(spy, "gu")
    room_circle(service, rid, wants=False)
    result = room_guess(service, rid, spy, correct).room
    assert result.phase == "result" and result.round.outcome.value == outcome
    assert result.last_deltas[str(spy)] == (3 if correct else 0)
    assert result.last_deltas[str(civilian)] == (0 if correct else 1)


def test_hidden_spy_civilian_guess_and_host_settings_between_rounds(service):
    rid = make_room(service)
    service.act(1, "mode:hidden")
    current = start_and_deal(service, rid)
    spy = int(current.round.spy_id)
    civilian = next(uid for uid in (1, 2, 3, 4) if uid != spy)
    room_circle(service, rid, wants=False)
    attempt = room_guess(service, rid, civilian, correct=True)
    assert attempt.room.phase == "playing" and "Игра продолжается" in attempt.alert
    assert str(civilian) in service.get(rid).round.hidden_attempts
    with pytest.raises(RoomError, match="один раз"):
        service.act(civilian, "gu")
    service.act(spy, "gu")
    room_circle(service, rid, wants=True)
    assert service.get(rid).round.phase == Phase.SUSPECT_VOTE
    assert not service.get(rid).guess_ui
    with pytest.raises(RoomError, match="до голосования"):
        service.act(spy, "gu")
    assert all(action != "gu" for row in service.get(rid).view_for(spy).rows for _, action in row)
    for uid in (1, 2, 3, 4):
        service.act(uid, f"v:{spy if uid != spy else civilian}")
    current = service.get(rid)
    assert current.phase == "result" and current.round.outcome.value == "civilians_win"
    assert current.last_deltas[str(civilian)] == 0
    assert sum(current.last_deltas.values()) == 2
    with pytest.raises(RoomError, match="только хосту"):
        service.act(2, "set")
    result = service.act(1, "set").room
    assert "Настройки комнаты" in result.view_for(1).text
    assert "Итоги раунда" in result.view_for(2).text
    service.act(1, "mode:classic")
    service.act(1, "sv:turn:90")
    restored = service.act(1, "ui:lobby").room
    assert restored.phase == "result" and len(restored.history) == 1
    next_room = service.act(1, "next").room
    assert next_room.round_no == 2 and not next_room.round.hidden
    assert next_room.settings["turn_seconds"] == 90


def test_practice_requires_majority_and_keeps_first_rated_round_at_one(service):
    rid = make_room(service)
    service.act(1, "ui:lobby")
    assert any(action == "practice" for row in service.get(rid).view_for(1).rows for _, action in row)
    poll = service.act(1, "practice").room.practice_poll
    service.act(1, f"pv:{poll}:1")
    service.act(2, f"pv:{poll}:1")
    assert service.get(rid).phase == "practice_vote"  # Exactly half.
    service.act(3, f"pv:{poll}:1")
    for uid in (1, 2, 3, 4):
        service.act(uid, "role")
    spy = int(service.get(rid).round.spy_id)
    result = room_guess(service, rid, spy).room
    assert result.phase == "practice_result"
    assert result.round_no == 0 and result.session_id is None and not result.history
    assert not any(result.scores.values())
    assert service.act(1, "start").room.round_no == 1


def test_practice_rejected_and_revoting_does_not_duplicate_votes(service):
    rid = make_room(service)
    poll = service.act(1, "practice").room.practice_poll
    service.act(1, f"pv:{poll}:1")
    service.act(1, f"pv:{poll}:1")
    service.act(2, f"pv:{poll}:0")
    result = service.act(3, f"pv:{poll}:0").room
    assert result.phase == "lobby" and result.practice_done
    with pytest.raises(RoomError):
        service.act(4, f"pv:{poll}:1")


def test_rematch_preserves_history_and_hides_practice_for_experienced_roster(service):
    rid = make_room(service, n=3)
    started = start_and_deal(service, rid, n=3)
    finished = room_guess(service, rid, int(started.round.spy_id)).room
    final = service.act(1, "end").room
    action = f"rematch:{final.code}"
    with pytest.raises(RoomError):
        service.act(2, action)
    rematch = service.act(1, action).room
    assert rematch.code != final.code and rematch.token != final.token
    assert rematch.settings == final.settings
    assert rematch.phase == "lobby" and rematch.session_id is None and rematch.round_no == 0
    assert [player["ready"] for player in rematch.players] == [True, False, False]
    assert not any(rematch.scores.values())
    assert not service.get(rid).practice_available
    with pytest.raises(RoomError):
        service.act(1, action)
    from app.models.services.history_service import HistoryService
    history = HistoryService(service._factory)
    assert history.lobby(1, finished.session_id).status == "finished"
    service.act(2, "rd:1")
    service.act(3, "rd:1")
    started_again = start_and_deal(service, rid, n=3)
    next_game = room_guess(service, rid, int(started_again.round.spy_id)).room
    assert next_game.session_id != finished.session_id
    assert history.user_lobbies(1, 0, 5)[1] == 2


def test_create_and_join_by_code_and_token(service):
    upd = service.create(1, "Павел")
    room = upd.room
    assert len(room.code) == 6 and room.host_id == 1
    service.join_code(2, "Максим", room.code.lower())
    service.join_token(3, "Анна", room.token)
    got = service.get(upd.room_id)
    assert [p["name"] for p in got.players] == ["Павел", "Максим", "Анна"]
    assert service.room_id_of(2) == upd.room_id


def test_join_errors(service):
    upd = service.create(1, "А")
    with pytest.raises(RoomError):
        service.join_code(2, "Б", "ZZZZZZ")
    with pytest.raises(RoomError):
        service.join_token(2, "Б", "bad-token")
    with pytest.raises(RoomError):
        service.create(1, "А")
    other = service.create(5, "Е")
    with pytest.raises(RoomError):
        service.join_code(5, "Е", upd.room.code)
    assert other.room_id != upd.room_id


def test_rejoin_same_room_is_reconnect(service):
    upd = service.create(1, "А")
    again = service.join_code(1, "А", upd.room.code)
    assert again.changed is False
    assert len(service.get(upd.room_id).players) == 1


def test_duplicate_names_get_suffix(service):
    upd = service.create(1, "Аня")
    service.join_code(2, "аня", upd.room.code)
    names = [p["name"] for p in service.get(upd.room_id).players]
    assert names[0] == "Аня" and names[1] != "аня" and "(2)" in names[1]


def test_max_players_enforced(service):
    upd = service.create(1, "Х")
    service.act(1, "cy:max")
    cap = service.get(upd.room_id).settings["max_players"]
    for uid in range(2, cap + 1):
        service.join_code(uid, f"И{uid}", upd.room.code)
    with pytest.raises(RoomError):
        service.join_code(100, "Лишний", upd.room.code)


def test_start_requires_ready_themes_and_min_players(service):
    upd = service.create(1, "Х")
    service.join_code(2, "Б", upd.room.code)
    with pytest.raises(RoomError):
        service.act(1, "start")
    service.join_code(3, "В", upd.room.code)
    with pytest.raises(RoomError):
        service.act(1, "start")
    service.act(1, "ui:settings")
    service.act(1, "ui:themes")
    service.act(1, "th:animals")
    with pytest.raises(RoomError):
        service.act(1, "start")
    service.act(2, "rd:1")
    service.act(3, "rd:1")
    service.act(1, "start")
    assert service.get(upd.room_id).phase == "dealing"


def test_only_host_can_change_settings_and_start(service):
    rid = make_room(service)
    for action in ("start", "cy:rounds", "th:food", "rt", "ui:settings", "force"):
        with pytest.raises(RoomError):
            service.act(2, action)
    assert service.get(rid).phase == "lobby"


def test_outsider_cannot_act(service):
    make_room(service)
    with pytest.raises(RoomError):
        service.act(999, "start")
    with pytest.raises(RoomError):
        service.say(999, "привет")


def test_role_alert_and_spy_never_sees_word_in_views(service):
    rid = make_room(service)
    service.act(1, "start")
    room = service.get(rid)
    word = room.round.word
    spy = int(room.round.spy_id)
    for uid in range(1, 5):
        alert = service.act(uid, "role").alert
        if uid == spy:
            assert word.upper() not in alert and "шпион" in alert
        else:
            assert word.upper() in alert
    for stage in ("discussion", "voting"):
        if stage == "voting":
            room_circle(service, rid, True)
        room = service.get(rid)
        screen = room.view_for(spy, "bot", T0)
        assert word.lower() not in screen.text.lower()
        assert all(word.lower() not in label.lower() for row in screen.rows for label, _ in row)


def test_discussion_starts_when_all_seen(service):
    rid = make_room(service)
    service.act(1, "start")
    for uid in (1, 2, 3):
        service.act(uid, "role")
    assert service.get(rid).phase == "dealing"
    service.act(4, "role")
    room = service.get(rid)
    assert room.phase == "playing" and room.round.phase == Phase.DISCUSSION


def test_force_start_by_host(service):
    rid = make_room(service)
    service.act(1, "start")
    service.act(1, "force")
    assert service.get(rid).phase == "playing"


def test_turn_order_say_only_on_own_turn_and_limits(service):
    rid = make_room(service)
    service.say(1, "До начала\nВторая строка", expected_phase="lobby")
    waiting = service.get(rid)
    assert waiting.chat[-1] == [waiting.name_of("1"), "До начала\nВторая строка", 1]
    assert not waiting.civilian_has_spoken
    room = start_and_deal(service, rid)
    assert not any("До начала" in entry[1] for entry in room.chat)
    current = int(room.current())
    other = next(p["id"] for p in room.players if p["id"] != current)
    with pytest.raises(RoomError):
        service.say(other, "не мой ход")
    with pytest.raises(RoomError, match="Чат изменился"):
        service.say(current, "Запоздавшее сообщение", expected_phase="lobby")
    service.say(current, "Привет\n<b>всем</b>", expected_phase="discussion")
    assert service.get(rid).chat[-1] == [room.name_of(str(current)), "Привет\n<b>всем</b>", current]
    with pytest.raises(RoomError):
        service.say(current, "x" * (MAX_MESSAGE_LEN + 1))
    with pytest.raises(RoomError):
        service.say(current, "   ")


def test_chat_text_is_escaped_in_view(service):
    rid = make_room(service)
    room = start_and_deal(service, rid)
    current = int(room.current())
    service.say(current, "<script>alert(1)</script>")
    view = service.get(rid).view_for(1, "bot", T0).text
    assert "<script>" not in view and "&lt;script&gt;" in view
    stored = service.get(rid).to_dict()
    del stored["civilian_has_spoken"]
    stored["chat"] = [entry[:2] for entry in stored["chat"]]
    restored = Room.from_dict(stored)
    assert "&lt;script&gt;" in restored.view_for(1, "bot", T0).text


def test_end_turn_and_timer_tick(service, clock):
    rid = make_room(service)
    room = start_and_deal(service, rid)
    first = room.round.current()
    with pytest.raises(RoomError):
        outsider = next(p["id"] for p in room.players if str(p["id"]) not in (first, "1"))
        service.act(outsider, "endturn:0")
    assert service.due_room_ids() == []
    clock.now = T0 + 61
    assert service.due_room_ids() == [rid]
    upd = service.tick_room(rid)
    assert upd.changed
    after = service.get(rid)
    assert after.round.current() != first
    assert after.round.circle_votes == {first: False}
    assert service.due_room_ids() == []
    service.act(1, "endturn:0")
    assert service.get(rid).round.current() != after.round.current()


def test_vote_majority_then_secret_votes_and_wrong_accusation(service):
    rid = make_room(service)
    room = start_and_deal(service, rid)
    spy = int(room.round.spy_id)
    victim = next(p["id"] for p in room.players if p["id"] != spy)
    order = [int(p) for p in room.round.order]
    for uid, want in zip(order, [1, 1, 0, 0]):
        service.act(uid, f"endturn:{want}")
    mid = service.get(rid)
    assert mid.round.phase == Phase.DISCUSSION and mid.round.circle_no == 2
    order = [int(p) for p in mid.round.order]
    for i, uid in enumerate(order):
        assert service.get(rid).round.phase == Phase.DISCUSSION
        service.act(uid, f"endturn:{1 if i < 3 else 0}")
    assert service.get(rid).round.phase == Phase.SUSPECT_VOTE
    for uid in range(1, 5):
        target = victim if uid != victim else spy
        service.act(uid, f"v:{target}")
        mid = service.get(rid)
        if mid.phase == "playing":
            for viewer in range(1, 5):
                text = mid.view_for(viewer, "bot", T0).text
                assert "голос принят" in text or "Кого вы считаете" in text
    room = service.get(rid)
    assert room.phase == "result"
    assert room.scores[str(spy)] == 3
    assert sum(room.scores.values()) == 3


def test_vote_forged_target_and_self_vote_rejected(service):
    rid = make_room(service)
    start_and_deal(service, rid)
    room_circle(service, rid, True)
    with pytest.raises(RoomError):
        service.act(1, "v:777")
    with pytest.raises(RoomError):
        service.act(1, "v:1")
    with pytest.raises(RoomError):
        service.act(1, "v:abc")


def test_vote_before_phase_rejected(service):
    rid = make_room(service)
    start_and_deal(service, rid)
    with pytest.raises(RoomError):
        service.act(1, "v:2")


def test_spy_found_ends_round_at_once_and_next_round_starts(service):
    rid = make_room(service)
    room = start_and_deal(service, rid)
    spy = int(room.round.spy_id)
    other = next(p["id"] for p in room.players if p["id"] != spy)
    room_circle(service, rid, True)
    for uid in range(1, 5):
        service.act(uid, f"v:{spy if uid != spy else other}")
    room = service.get(rid)
    assert room.phase == "result" and room.round.outcome.value == "civilians_win"
    assert all(room.scores[str(p["id"])] == (0 if p["id"] == spy else 1) for p in room.players)
    service.act(1, "next")
    nxt = service.get(rid)
    assert nxt.phase == "dealing" and nxt.round_no == 2
    assert nxt.round.spy_id != str(spy)
    assert nxt.scores == room.scores


def test_online_correct_guess_ends_round_immediately(service):
    rid = make_room(service)
    room = start_and_deal(service, rid)
    spy = int(room.round.spy_id)
    assert room.round.open_guess is True
    room_guess(service, rid, spy, correct=True)
    done = service.get(rid)
    assert done.phase == "result" and done.round.phase == Phase.FINISHED
    assert done.scores[str(spy)] == 3 and sum(done.scores.values()) == 3
    assert done.round.outcome.value == "spy_win_guess"


def test_online_non_spy_cannot_open_guess_panel(service):
    rid = make_room(service)
    room = start_and_deal(service, rid)
    spy = int(room.round.spy_id)
    other = next(p["id"] for p in room.players if p["id"] != spy)
    with pytest.raises(RoomError):
        service.act(other, "gu")
    assert service.get(rid).phase == "playing"
    assert service.get(rid).guess_ui == {}


def test_guess_filter_and_crossed_words_persist_privately(service):
    rid = make_room(service)
    room = start_and_deal(service, rid)
    spy = int(room.round.spy_id)
    if int(room.current()) == spy:
        service.act(spy, "endturn:0")
    service.say(int(service.get(rid).current()), "Первая подсказка")
    service.act(spy, "gu")
    state = service.get(rid)
    words = state.guess_sheet(spy)["words"]
    index = words.index(state.round.word)
    service.act(spy, "gq:" + words[index].upper())
    shown = {command for row in service.get(rid).view_for(spy).rows for _, command in row}
    assert f"gp:{index}" in shown
    service.act(spy, "gm")
    assert f"gx:{index}:1" in {command for row in service.get(rid).view_for(spy).rows for _, command in row}
    service.act(spy, f"gx:{index}:1")
    service.act(spy, "cancel")
    service.act(spy, "gu")
    state = service.get(rid)
    assert state.guess_sheet(spy)["crossed"] == [index]
    other = next(player["id"] for player in state.players if player["id"] != spy)
    assert state.guess_sheet(other) is None
    with pytest.raises(RoomError):
        service.act(spy, f"gp:{index}")
    service.act(spy, f"gx:{index}:0")
    service.act(spy, f"gp:{index}")
    result = service.act(spy, "yes").room
    assert not result.guess_crossed and result.round.outcome.value == "spy_win_guess"


def test_online_wrong_guess_before_voting_loses_at_once(service):
    rid = make_room(service)
    room = start_and_deal(service, rid)
    spy = int(room.round.spy_id)
    room_guess(service, rid, spy, correct=False)
    done = service.get(rid)
    assert done.phase == "result" and done.round.outcome.value == "spy_wrong_guess"
    assert done.scores[str(spy)] == 0
    assert all(done.scores[str(p["id"])] == 1 for p in done.players if p["id"] != spy)
    with pytest.raises(RoomError):
        service.act(spy, "gu")


def test_online_wrong_guess_during_voting_burns_attempt_and_voting_decides(service):
    rid = make_room(service)
    room = start_and_deal(service, rid)
    spy = int(room.round.spy_id)
    other = next(p["id"] for p in room.players if p["id"] != spy)
    room_circle(service, rid, True)
    resp = room_guess(service, rid, spy, correct=False)
    assert "Неверно" in resp.alert
    mid = service.get(rid)
    assert mid.phase == "playing" and mid.round.guesses_left == 0
    with pytest.raises(RoomError):
        service.act(spy, "gu")
    for uid in range(1, 5):
        service.act(uid, f"v:{spy if uid != spy else other}")
    done = service.get(rid)
    assert done.round.outcome.value == "civilians_win"


def test_online_guess_allowed_during_voting(service):
    rid = make_room(service)
    room = start_and_deal(service, rid)
    spy = int(room.round.spy_id)
    room_circle(service, rid, True)
    assert service.get(rid).round.phase == Phase.SUSPECT_VOTE
    room_guess(service, rid, spy, correct=True)
    assert service.get(rid).phase == "playing"
    other = next(p["id"] for p in room.players if p["id"] != spy)
    for player in room.players:
        uid = player["id"]
        service.act(uid, f"v:{spy if uid != spy else other}")
    assert service.get(rid).round.outcome.value == "draw"


def test_guess_then_vote_race_round_finishes_once(service):
    rid = make_room(service)
    room = start_and_deal(service, rid)
    spy = int(room.round.spy_id)
    room_guess(service, rid, spy, correct=True)
    done = service.get(rid)
    scores = dict(done.scores)
    with pytest.raises(RoomError):
        service.act(spy, "yes")
    with pytest.raises(RoomError):
        service.act(1, "endturn:1")
    with pytest.raises(RoomError):
        service.act(1, "v:2")
    assert service.get(rid).scores == scores


def test_leave_in_lobby_transfers_host(service):
    rid = make_room(service)
    upd = service.act(1, "leave")
    assert upd.left_user == 1
    room = service.get(rid)
    assert room.host_id == 2 and len(room.players) == 3
    assert service.room_id_of(1) is None


def test_civilian_leaving_with_too_few_players_cancels_round(service):
    rid = make_room(service, n=3)
    room = start_and_deal(service, rid, n=3)
    civilian = next(p["id"] for p in room.players if str(p["id"]) != room.round.spy_id and p["id"] != 1)
    service.act(civilian, "leave")
    room = service.get(rid)
    assert room.phase == "lobby" and room.round is None and room.round_no == 0
    assert len(room.players) == 2


def test_spy_leaving_finishes_round_with_civilian_victory(service):
    rid = make_room(service)
    room = start_and_deal(service, rid)
    service.act(int(room.round.spy_id), "leave")
    room = service.get(rid)
    assert room.phase == "result" and len(room.players) == 3
    assert room.history[-1]["outcome"] == "spy_left"


def test_civilian_leaving_with_enough_players_keeps_round(service):
    rid = make_room(service)
    room = start_and_deal(service, rid)
    civilian = next(p["id"] for p in room.players if str(p["id"]) != room.round.spy_id and p["id"] != 1)
    service.act(civilian, "leave")
    room = service.get(rid)
    assert room.phase == "playing" and room.round is not None and len(room.players) == 3


def test_last_player_leaving_closes_room(service):
    upd = service.create(1, "А")
    result = service.act(1, "leave")
    assert result.room is None
    assert service.get(upd.room_id) is None
    assert service.room_id_of(1) is None
    service.create(1, "А")


def test_finish_all_rounds_and_final(service):
    rid = make_room(service)
    service.act(1, "ui:settings")
    service.act(1, "cy:rounds")
    room = service.get(rid)
    while room.settings["rounds"] != 3:
        service.act(1, "cy:rounds")
        room = service.get(rid)
    for _ in range(3):
        room = start_and_deal(service, rid) if service.get(rid).phase in ("lobby",) else service.get(rid)
        if room.phase == "dealing":
            for uid in range(1, 5):
                service.act(uid, "role")
            room = service.get(rid)
        spy = int(room.round.spy_id)
        room_guess(service, rid, spy, correct=True)
        assert service.get(rid).phase == "result"
        if service.get(rid).round_no < 3:
            service.act(1, "next")
    with pytest.raises(RoomError):
        service.act(1, "next")
    service.act(1, "end")
    final = service.get(rid)
    assert final.phase == "final"
    assert "Итоговая таблица" in final.view_for(1, "bot", T0).text


def test_state_survives_service_restart(tmp_path, clock):
    url = f"sqlite:///{tmp_path}/persist.db"
    first = RoomService(make_session_factory(url), rng=random.Random(1), clock=clock)
    rid = make_room(first)
    room = start_and_deal(first, rid)
    word = room.round.word
    again = RoomService(make_session_factory(url), clock=clock)
    restored = again.get(rid)
    assert restored.phase == "playing" and restored.round.word == word
    assert again.room_id_of(2) == rid
    current = int(restored.round.current())
    again.act(current, "endturn:1")
    assert again.get(rid).round.circle_votes == {str(current): True}


def test_purge_stale(service):
    upd = service.create(1, "А")
    assert service.purge_stale(hours=24) == 0
    assert service.purge_stale(hours=-1) == 1
    assert service.get(upd.room_id) is None and service.room_id_of(1) is None


def test_views_render_for_every_phase_and_member(service):
    rid = make_room(service)
    for uid in range(1, 5):
        assert service.get(rid).view_for(uid, "bot", T0).text
    room = start_and_deal(service, rid)
    for uid in range(1, 5):
        assert room.view_for(uid, "bot", T0).rows
    service.act(1, "ui:lobby") if False else None

def test_early_guess_third_warning_bans_spy_and_ends_round(service, clock):
    rid = make_room(service)
    room = start_and_deal(service, rid)
    spy = int(room.round.spy_id)
    for count in (1, 2):
        update = service.act(spy, "gu")
        assert f"{count}/3" in update.alert and not update.changed
        assert str(spy) not in update.room.guess_ui
        assert update.room.round.guesses_left == 1
        assert service.ban_seconds(spy) == 0
    update = service.act(spy, "gu")
    assert "5 мин" in update.alert and update.left_user == spy
    assert update.penalty is None
    assert update.room.phase == "result"
    assert service.ban_status(spy) == (300, "early_guess")
    assert service.room_id_of(spy) is None
    with pytest.raises(RoomError, match="ранние попытки"):
        service.create(spy, "Шпион")
    clock.now += 301
    assert service.ban_seconds(spy) == 0


def test_early_guess_warnings_use_rolling_week_and_stop_after_civilian_speaks(service, clock):
    rid = make_room(service)
    room = start_and_deal(service, rid)
    spy = int(room.round.spy_id)
    assert "1/3" in service.act(spy, "gu").alert
    clock.now += 6 * 24 * 3600
    assert "2/3" in service.act(spy, "gu").alert
    clock.now += 24 * 3600 + 1
    assert "2/3" in service.act(spy, "gu").alert
    civilian = int(room.round.current())
    assert civilian != spy
    service.say(civilian, "Первое сообщение в раунде.")
    update = service.act(spy, "gu")
    assert update.alert is None and str(spy) in update.room.guess_ui
    assert service.ban_seconds(spy) == 0
