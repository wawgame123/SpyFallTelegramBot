from tests.pack_fixtures import test_library
import random

from app.models.database.repository import LocalGameRepository
from app.models.database.session import make_session_factory
from app.models.game.session import LocalSession, Stage
from tests.helpers import local_circle, local_guess

NAMES = ["Павел", "Максим", "Анна", "Илья", "Катя"]


def setup_session(n=4, seed=1, themes=("animals",)):
    s = LocalSession(rng=random.Random(seed), library=test_library())
    s.act(s.seq, f"n:{n}")
    for i in range(n):
        s.handle_text(NAMES[i])
    s.act(s.seq, "th")
    for t in themes:
        s.act(s.seq, f"t:{t}")
    s.act(s.seq, "ok")
    s.act(s.seq, "go")
    return s


def deal(s):
    seen = []
    for _ in range(len(s.names)):
        assert s.stage == Stage.DEAL_HANDOFF
        s.act(s.seq, "me")
        assert s.stage == Stage.DEAL_REVEAL
        seen.append(s.act(s.seq, "show").alert)
        s.act(s.seq, "next")
    assert s.stage == Stage.DEAL_DONE
    s.act(s.seq, "start")
    assert s.stage == Stage.DISCUSSION
    return seen


def vote_all(s, target_for):
    for _ in range(len(s.names)):
        voter = s.round.players[s.vote_idx]
        s.act(s.seq, "me")
        s.act(s.seq, f"v:{target_for(voter)}")
        if s.stage in (Stage.RESULT, Stage.GUESS_HANDOFF):
            break


def to_voting(s):
    local_circle(s, True)
    assert s.stage == Stage.VOTE_HANDOFF


def test_setup_validation():
    s = LocalSession(rng=random.Random(1), library=test_library())
    assert s.act(s.seq, "n:2").alert
    s.act(s.seq, "n:3")
    assert s.stage == Stage.NAMES
    s.handle_text("   ")
    assert s.stage == Stage.NAMES and s.notice
    s.handle_text("Аня")
    s.handle_text("аня")
    assert len(s.names) == 1
    s.handle_text("Боря")
    s.handle_text("Вика")
    assert s.stage == Stage.REVIEW
    assert s.act(s.seq, "go").alert == "Выберите хотя бы одну тему"


def test_edit_name_before_start():
    s = LocalSession(rng=random.Random(1), library=test_library())
    s.act(s.seq, "n:3")
    for n in ["А", "Б", "В"]:
        s.handle_text(n)
    s.act(s.seq, "e:1")
    s.handle_text("Борис")
    assert s.names == ["А", "Борис", "В"]


def test_roles_distribution_and_hidden_until_confirm():
    s = setup_session()
    assert s.screen().rows == [[("Показать роль", "me")]]
    assert s.act(s.seq, "show").alert == "Роль сейчас недоступна"
    seen = deal(s)
    spies = [a for a in seen if "шпион" in a]
    assert len(spies) == 1
    word = s.round.word.upper()
    assert sum(word in a for a in seen) == 3
    assert word not in spies[0]
    assert all("Тема" not in a for a in seen)


def test_theme_shown_after_roles_dealt():
    s = setup_session()
    for _ in range(len(s.names)):
        s.act(s.seq, "me")
        s.act(s.seq, "show")
        s.act(s.seq, "next")
    assert s.stage == Stage.DEAL_DONE
    assert s.round.theme in s.screen().text


def test_next_requires_viewing_role():
    s = setup_session()
    s.act(s.seq, "me")
    assert s.act(s.seq, "next").alert == "Сначала посмотрите свою роль"
    assert s.stage == Stage.DEAL_REVEAL


def test_previous_role_hidden_and_no_back():
    s = setup_session()
    s.act(s.seq, "me")
    s.act(s.seq, "show")
    old_seq = s.seq
    s.act(s.seq, "next")
    screen = s.screen()
    assert s.round.word.upper() not in screen.text
    assert "Максим" in screen.text
    actions = [a for row in screen.rows for _, a in row]
    assert "back" not in actions and "show" not in actions
    stale = s.act(old_seq, "show")
    assert stale.alert == "Эта кнопка устарела" and stale.screen.text == screen.text


def test_secret_never_in_public_screens():
    s = setup_session()
    deal(s)
    word = s.round.word
    assert word.lower() not in s.screen().text.lower()
    to_voting(s)
    assert word.lower() not in s.screen().text.lower()


def test_voting_starts_only_after_full_circle():
    s = setup_session()
    deal(s)
    for _ in range(3):
        s.act(s.seq, "nx:1")
        assert s.stage == Stage.DISCUSSION
    s.act(s.seq, "nx:1")
    assert s.stage == Stage.VOTE_HANDOFF
    assert "большинство за голосование" in s.screen().text


def test_no_majority_starts_new_circle_with_notice():
    s = setup_session()
    deal(s)
    for want in (1, 1, 0, 0):
        s.act(s.seq, f"nx:{want}")
    assert s.stage == Stage.DISCUSSION
    assert s.round.circle_no == 2
    assert "большинство против голосования" in s.screen().text
    assert "круг 2" in s.screen().text


def test_each_turn_offers_vote_choice_buttons():
    s = setup_session()
    deal(s)
    actions = [a for row in s.screen().rows for _, a in row]
    assert "nx:0" in actions and "nx:1" in actions and "vr" not in actions


def test_full_round_civilians_win_and_next_round_keeps_names_and_scores():
    s = setup_session(seed=3)
    deal(s)
    spy = s.round.spy_id
    other = next(p for p in s.pids if p != spy)
    to_voting(s)
    vote_all(s, lambda voter: spy if voter != spy else other)
    assert s.stage == Stage.GUESS_HANDOFF
    s.act(s.seq, "skip")
    assert s.stage == Stage.RESULT
    assert s.scores[spy] == 0
    assert all(s.scores[p] == 1 for p in s.pids if p != spy)
    s.act(s.seq, "nr")
    assert s.stage == Stage.DEAL_HANDOFF
    assert s.names == NAMES[:4]
    assert sum(s.scores.values()) == 3
    assert s.round.spy_id != spy
    assert s.round_no == 2


def test_spy_wins_on_wrong_accusation():
    s = setup_session(seed=4)
    deal(s)
    spy = s.round.spy_id
    victim = next(p for p in s.pids if p != spy)
    to_voting(s)
    vote_all(s, lambda voter: victim if voter != victim else spy)
    assert s.stage == Stage.RESULT
    assert s.scores[spy] == 3
    assert sum(s.scores.values()) == 3


def test_discussion_guess_uses_the_same_screen_for_both_roles():
    s = setup_session(seed=8)
    deal(s)
    spy = s.round.spy_id
    screens = {}
    for _ in s.pids:
        actor = s.round.current()
        s.act(s.seq, "gu")
        idx = s._guess_words().index(s.round.word)
        s.act(s.seq, f"pg:{idx // 8}")
        pick = s.screen()
        s.act(s.seq, f"gp:{idx}")
        confirm = s.screen()
        screens[actor == spy] = (pick, confirm)
        if actor != spy:
            s.act(s.seq, "yes")
            assert s.stage == Stage.DISCUSSION and s.round.guesses_left == 1
        else:
            s.act(s.seq, "no")
            s.act(s.seq, "cancel")
        s.act(s.seq, "nx:0")
    assert screens[True] == screens[False]


def test_spy_guess_during_discussion_finishes_immediately():
    for correct in (True, False):
        s = setup_session(seed=8)
        deal(s)
        while s.round.current() != s.round.spy_id:
            s.act(s.seq, "nx:0")
        s.act(s.seq, "gu")
        words = s._guess_words()
        idx = words.index(s.round.word) if correct else next(
            i for i, word in enumerate(words) if word != s.round.word
        )
        s.act(s.seq, f"pg:{idx // 8}")
        s.act(s.seq, f"gp:{idx}")
        s.act(s.seq, "yes")
        assert s.stage == Stage.RESULT
        assert s.round.outcome.value == ("spy_win_guess" if correct else "spy_wrong_guess")


def test_last_chance_after_spy_is_caught():
    s = setup_session(seed=8)
    deal(s)
    to_voting(s)
    assert "gu" not in [action for row in s.screen().rows for _, action in row]
    assert s.act(s.seq, "gu").alert

    spy = s.round.spy_id
    other = next(p for p in s.pids if p != spy)
    vote_all(s, lambda voter: spy if voter != spy else other)
    assert s.stage == Stage.GUESS_HANDOFF
    assert s.round.outcome is None
    restored = LocalSession.from_dict(s.to_dict(), library=s.library)
    local_guess(restored, correct=True)
    assert restored.stage == Stage.RESULT
    assert restored.round.outcome.value == "draw"
    assert set(restored.scores.values()) == {0}


def test_legacy_local_guess_resumes_voting_without_spending_last_chance():
    s = setup_session(seed=8)
    deal(s)
    to_voting(s)
    saved = s.to_dict()
    saved["stage"] = "guess_confirm"
    saved["guess_return"] = Stage.VOTE_HANDOFF.value
    saved["guess_word"] = s.round.word
    saved["round"].pop("guess_after_capture")
    saved["round"]["guesses_left"] = 0
    saved["round"]["secret_guess_correct"] = False
    restored = LocalSession.from_dict(saved, library=s.library)
    assert restored.stage == Stage.VOTE_HANDOFF
    assert restored.round.guesses_left == 1


def test_runoff_flow():
    s = setup_session(n=4, seed=7)
    deal(s)
    to_voting(s)
    p0, p1, p2, p3 = s.pids
    plan = {p0: p1, p1: p0, p2: p0, p3: p1}
    for _ in range(4):
        voter = s.round.players[s.vote_idx]
        s.act(s.seq, "me")
        s.act(s.seq, f"v:{plan[voter]}")
    assert s.round.phase.value == "runoff_vote"
    assert "Дополнительное голосование" in s.screen().text
    assert s.round.votes == {}


def test_vote_screen_excludes_self():
    s = setup_session(seed=8)
    deal(s)
    to_voting(s)
    s.act(s.seq, "me")
    labels = [label for row in s.screen().rows for label, _ in row]
    assert "Павел" not in labels and len(labels) == 3


def test_results_screen_and_end_game():
    s = setup_session(seed=9)
    deal(s)
    spy = s.round.spy_id
    victim = next(p for p in s.pids if p != spy)
    to_voting(s)
    vote_all(s, lambda voter: victim if voter != victim else spy)
    text = s.screen().text
    assert s.round.word in text and s.name_of(spy) in text
    s.act(s.seq, "end")
    assert s.stage == Stage.FINAL
    assert s.act(s.seq, "menu").exit


def test_change_settings_keeps_scores():
    s = setup_session(seed=10)
    deal(s)
    spy = s.round.spy_id
    victim = next(p for p in s.pids if p != spy)
    to_voting(s)
    vote_all(s, lambda voter: victim if voter != victim else spy)
    s.act(s.seq, "set")
    assert s.stage == Stage.REVIEW
    s.act(s.seq, "go")
    assert s.scores[spy] == 3 and s.round_no == 2


def test_restore_starts_from_neutral_screen(tmp_path):
    repo = LocalGameRepository(make_session_factory(f"sqlite:///{tmp_path}/t.db"))
    s = setup_session(seed=11)
    s.act(s.seq, "me")
    s.act(s.seq, "show")
    repo.save(1, s.to_dict())
    restored = LocalSession.from_dict(repo.load(1))
    assert restored.stage == Stage.DEAL_REVEAL
    restored.neutralize()
    assert restored.stage == Stage.DEAL_HANDOFF
    assert restored.round.word.upper() not in restored.screen().text
    assert restored.names == s.names


def test_legacy_vote_request_stage_maps_to_discussion():
    s = setup_session(seed=12)
    deal(s)
    data = s.to_dict()
    data["stage"] = "vote_request"
    assert LocalSession.from_dict(data).stage == Stage.DISCUSSION


def test_users_are_isolated(tmp_path):
    repo = LocalGameRepository(make_session_factory(f"sqlite:///{tmp_path}/t.db"))
    repo.save(1, setup_session(seed=1).to_dict())
    assert repo.load(2) is None
    repo.delete(1)
    assert repo.load(1) is None


def pick_themes(s, ids, random_order=False):
    s.act(s.seq, "th")
    for theme in ids:
        s.act(s.seq, f"t:{theme}")
    if random_order:
        s.act(s.seq, "rt")
    s.act(s.seq, "ok")


def finish_round_quickly(s):
    from app.models.game.scoring import Outcome

    s.round.phase = s.round.phase.__class__.FINISHED
    s.round.outcome = Outcome.DRAW
    s.stage = Stage.RESULT
    s.history.append({"round": s.round_no, "spy": s.round.spy_id, "word": s.round.word, "outcome": "draw", "deltas": {}})


def test_selected_themes_rotate_one_per_round():
    s = LocalSession(rng=random.Random(2), library=test_library())
    s.act(s.seq, "n:3")
    for n in NAMES[:3]:
        s.handle_text(n)
    pick_themes(s, ["animals", "food", "sport"])
    s.act(s.seq, "go")
    seen = []
    for _ in range(4):
        assert s.round.theme_id in ("animals", "food", "sport")
        assert len(s.round.allowed_words) == 20
        assert set(s.round.allowed_words) <= set(s.library.get(s.round.theme_id).words)
        seen.append(s.round.theme_id)
        finish_round_quickly(s)
        s.act(s.seq, "nr")
    assert seen == ["animals", "food", "sport", "animals"]
    assert "Смешанная тема" not in s.round.theme


def test_random_theme_order_avoids_immediate_repeat():
    s = LocalSession(rng=random.Random(3), library=test_library())
    s.act(s.seq, "n:3")
    for n in NAMES[:3]:
        s.handle_text(n)
    pick_themes(s, ["animals", "food", "sport"], random_order=True)
    s.act(s.seq, "go")
    previous = None
    seen = set()
    for _ in range(12):
        assert s.round.theme_id != previous
        previous = s.round.theme_id
        seen.add(previous)
        finish_round_quickly(s)
        s.act(s.seq, "nr")
    assert len(seen) == 3


def test_single_theme_is_used_every_round():
    s = setup_session(seed=5, themes=("animals",))
    for _ in range(3):
        assert s.round.theme_id == "animals"
        finish_round_quickly(s)
        s.act(s.seq, "nr")


def test_theme_screen_explains_order_and_shows_label():
    s = LocalSession(rng=random.Random(1), library=test_library())
    s.act(s.seq, "n:3")
    for n in NAMES[:3]:
        s.handle_text(n)
    s.act(s.seq, "th")
    labels = [label for row in s.screen().rows for label, _ in row]
    assert "Порядок тем: по очереди" in labels
    s.act(s.seq, "rt")
    labels = [label for row in s.screen().rows for label, _ in row]
    assert "Порядок тем: случайный" in labels
    assert not any("Случайная тема" in label for label in labels)


def test_words_not_repeated_until_pool_exhausted():
    from app.models.game.scoring import Outcome

    s = setup_session(seed=13)
    seen = {s.round.word}
    for _ in range(5):
        s.round.phase = s.round.phase.__class__.FINISHED
        s.round.outcome = s.round.outcome or Outcome.DRAW
        s.stage = Stage.RESULT
        s.history.append({"round": s.round_no, "spy": s.round.spy_id, "word": s.round.word, "outcome": "draw", "deltas": {}})
        s.act(s.seq, "nr")
        assert s.round.word not in seen
        seen.add(s.round.word)
