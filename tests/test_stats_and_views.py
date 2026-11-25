from tests.pack_fixtures import DEFAULT_PACKS
import random
from datetime import datetime, timedelta, timezone

import pytest

from app.views.telegram import pack_view, stats_view
from app.views.telegram.keyboards.reply import ALL_BUTTONS, main_reply_keyboard
from app.models.database.models import GameSession, MatchRound, RoundPlayer
from app.models.database.pack_repository import PackRepository
from app.models.database.session import make_session_factory
from app.models.game.session import LocalSession
from app.models.services.pack_service import PackService
from app.models.services.room_service import RoomService
from app.models.services.stats_service import MIN_GAMES_FOR_RATE, StatsService
from tests.helpers import room_circle, room_guess


@pytest.fixture
def factory(tmp_path):
    return make_session_factory(f"sqlite:///{tmp_path}/s.db")


def add_round(factory, session_id, round_no, spy, results):
    with factory.begin() as db:
        match = MatchRound(
            session_id=session_id,
            round_no=round_no,
            theme="t",
            word="w",
            spy_user_id=spy,
            spy_name=f"U{spy}",
            outcome="civilians_win",
        )
        db.add(match)
        db.flush()
        for uid, (result, delta) in results.items():
            db.add(
                RoundPlayer(
                    round_id=match.id,
                    user_id=uid,
                    name=f"U{uid}",
                    role="spy" if uid == spy else "civilian",
                    result=result,
                    score_delta=delta,
                )
            )


def add_session(factory):
    with factory.begin() as db:
        session = GameSession(room_code="ABC123", title="Комната", host_id=1, status="finished")
        db.add(session)
        db.flush()
        return session.id


def test_profile_counts(factory):
    sid = add_session(factory)
    add_round(factory, sid, 1, 1, {1: ("win", 3), 2: ("loss", 0), 3: ("loss", 0)})
    add_round(factory, sid, 2, 2, {1: ("win", 1), 2: ("loss", 0), 3: ("win", 1)})
    add_round(factory, sid, 3, 3, {1: ("draw", 0), 2: ("draw", 0), 3: ("draw", 0)})
    stats = StatsService(factory)
    p = stats.profile(1)
    assert (p.games, p.wins, p.spy_wins, p.civilian_wins) == (3, 2, 1, 1)
    assert (p.losses, p.draws, p.points) == (0, 1, 4)
    assert p.win_rate == 66.7
    assert stats.profile(999) is None


def test_leaderboard_sorts_and_min_games_for_rate(factory):
    sid = add_session(factory)
    for n in range(1, MIN_GAMES_FOR_RATE + 1):
        add_round(factory, sid, n, 2, {1: ("win", 1), 2: ("loss", 0), 3: ("loss", 0)})
    add_round(factory, sid, 10, 3, {1: ("loss", 0), 2: ("loss", 0), 3: ("win", 3)})
    stats = StatsService(factory)
    by_points = stats.leaderboard("points")
    assert [r.user_id for r in by_points][:2] == [1, 3] or by_points[0].points >= by_points[1].points
    assert by_points[0].user_id == 1 and by_points[0].points == MIN_GAMES_FOR_RATE
    assert stats.leaderboard("wins")[0].user_id == 1
    assert stats.leaderboard("spy")[0].user_id == 3
    rate = stats.leaderboard("rate")
    assert {r.user_id for r in rate} == {1, 2, 3}
    assert rate[0].user_id == 1
    for row in rate:
        assert row.games >= MIN_GAMES_FOR_RATE


def test_rate_excludes_players_with_few_games(factory):
    sid = add_session(factory)
    add_round(factory, sid, 1, 2, {1: ("win", 1), 2: ("loss", 0)})
    assert StatsService(factory).leaderboard("rate") == []
    assert StatsService(factory).leaderboard("bogus")[0].user_id == 1


def test_rating_periods_and_viewer_outside_top_ten(factory):
    now = datetime.now(timezone.utc)
    sid = add_session(factory)
    add_round(factory, sid, 1, 1, {uid: ("win", 1) for uid in range(1, 13)})
    add_round(factory, sid, 2, 12, {12: ("win", 3)})
    add_round(factory, sid, 3, 13, {13: ("win", 3)})
    with factory.begin() as db:
        matches = {match.round_no: match for match in db.query(MatchRound)}
        matches[2].finished_at = now - timedelta(days=10)
        matches[3].finished_at = now - timedelta(days=40)
    stats = StatsService(factory)
    rows, rank = stats.rating("points", "week", 12)
    assert len(rows) == 10 and rank.place == 12
    assert "Ваше место: 12" in stats_view.rating_screen(rows, "points", 12, "week", rank).text
    rows, rank = stats.rating("points", "month", 12)
    assert rows[0].user_id == 12 and rank is None
    assert all(row.user_id != 13 for row in rows)
    assert any(row.user_id == 13 for row in stats.leaderboard())


def test_stats_from_real_online_games(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path}/real.db")
    service = RoomService(factory, rng=random.Random(2))
    upd = service.create(1, "Хост")
    for uid in (2, 3):
        service.join_code(uid, f"И{uid}", upd.room.code)
    service.act(1, "ui:settings")
    service.act(1, "th:animals")
    service.act(2, "rd:1")
    service.act(3, "rd:1")
    service.act(1, "start")
    for uid in (1, 2, 3):
        service.act(uid, "role")
    spy = int(service.get(upd.room_id).round.spy_id)
    room_guess(service, upd.room_id, spy, correct=True)
    stats = StatsService(factory)
    assert stats.profile(spy).spy_wins == 1 and stats.profile(spy).points == 3
    assert stats.leaderboard("points")[0].user_id == spy


def test_stats_screens(factory):
    empty = stats_view.profile_screen(None, "Аня")
    assert "ещё не сыграли" in empty.text
    sid = add_session(factory)
    add_round(factory, sid, 1, 1, {1: ("win", 3), 2: ("loss", 0), 3: ("loss", 0)})
    stats = StatsService(factory)
    text = stats_view.profile_screen(stats.profile(1), "Аня").text
    assert "Победы: 1" in text and "Всего очков: 3" in text
    rating = stats_view.rating_screen(stats.leaderboard("points"), "points", viewer_id=1)
    assert "(вы)" in rating.text and "[x] По очкам" in rating.rows[0][0][0]
    assert "нет данных" in stats_view.rating_screen([], "rate").text


@pytest.fixture
def packs(tmp_path):
    f = make_session_factory(f"sqlite:///{tmp_path}/v.db")
    repo = PackRepository(f)
    repo.seed(DEFAULT_PACKS)
    return PackService(f, repo.load_library())


def all_actions(screen):
    return [action for row in screen.rows for _, action in row]


def test_pack_card_buttons_depend_on_role(packs):
    pid = packs.create_pack(1, "Автор", "Набор", [f"с{i}" for i in range(12)])
    packs.set_public(1, pid, True)
    own = all_actions(pack_view.card_screen(packs.info(1, pid), 1))
    other = all_actions(pack_view.card_screen(packs.info(2, pid), 2))
    assert f"pk:del:{pid}" in own and f"pk:add:{pid}" in own and f"pk:vis:{pid}" in own
    assert f"pk:del:{pid}" not in other and f"pk:rep:{pid}" in other
    builtin = packs.list_packs(2, "builtin")[0][0]
    built = all_actions(pack_view.card_screen(builtin, 2))
    assert f"pk:rep:{builtin.id}" not in built and f"pk:del:{builtin.id}" not in built


def test_pack_card_warns_about_few_words_and_empty(packs):
    few = packs.create_pack(1, "А", "Малый", ["один", "два"])
    empty = packs.create_pack(1, "А", "Пустой", [])
    assert "Слов мало" in pack_view.card_screen(packs.info(1, few), 1).text
    assert "Набор пуст" in pack_view.card_screen(packs.info(1, empty), 1).text


def test_pack_list_and_words_screens(packs):
    for i in range(8):
        packs.create_pack(1, "А", f"Набор {i}", ["слово"])
    infos, total = packs.list_packs(1, "my", 0, pack_view.PAGE_SIZE)
    screen = pack_view.list_screen("my", infos, total, 0)
    assert any(a == "pk:list:my:1" for a in all_actions(screen))
    assert "pk:new" in all_actions(screen)
    empty = pack_view.list_screen("fav", [], 0, 0)
    assert "пусто" in empty.text
    filtered = pack_view.list_screen("builtin", infos, total, 0, "кот", "name", "Еда")
    assert "поиск: кот" in filtered.text and "pk:clear:builtin" in all_actions(filtered)
    info = packs.info(1, infos[0].id)
    words, wtotal = packs.list_words(1, info.id, 0, 10)
    own_view = pack_view.words_screen(info, words, wtotal, 0, True)
    other_view = pack_view.words_screen(info, words, wtotal, 0, False)
    assert any(a.startswith("pk:w:") for a in all_actions(own_view))
    assert not any(a.startswith("pk:w:") for a in all_actions(other_view))
    assert "слово" in other_view.text


def test_moderation_screens(packs):
    pid = packs.create_pack(1, "А", "Спорный", [f"с{i}" for i in range(12)])
    packs.set_public(1, pid, True)
    for reporter in (2, 3, 4):
        packs.report(reporter, pid, "spam")
    items, total = packs.moderation_list(0, 6)
    screen = pack_view.moderation_screen(items, total, 0)
    assert f"pk:modv:{pid}" in all_actions(screen)
    info, reasons = packs.moderation_info(pid)
    card = pack_view.moderation_card(info, reasons)
    assert {f"pk:modr:{pid}", f"pk:modh:{pid}", f"pk:modd:{pid}"} <= set(all_actions(card))
    assert "Жалоб нет" in pack_view.moderation_screen([], 0, 0).text


def test_callback_data_fits_telegram_limit(packs, factory):
    pid = packs.create_pack(1, "А", "Набор", [f"с{i}" for i in range(12)])
    packs.set_public(1, pid, True)
    info = packs.info(2, pid)
    screens = [
        pack_view.menu_screen(),
        pack_view.card_screen(info, 2),
        pack_view.card_screen(packs.info(1, pid), 1),
        pack_view.report_screen(info),
        pack_view.delete_screen(info),
        pack_view.category_screen("public", packs.categories()),
        pack_view.word_screen(123456, "слово", 123456),
        pack_view.list_screen("public", *packs.list_packs(2, "public"), 0),
        stats_view.rating_screen([], "points"),
    ]
    for screen in screens:
        for action in all_actions(screen):
            assert len(f"lg:12345:{action}".encode()) <= 64
            assert len(action.encode()) <= 64
    local = LocalSession(rng=random.Random(1), library=packs.library_for(1))
    local.act(local.seq, "n:3")
    for name in ["А", "Б", "В"]:
        local.handle_text(name)
    local.act(local.seq, "th")
    for action in all_actions(local.screen()):
        assert len(f"lg:123456:{action}".encode()) <= 64


def test_room_callback_data_fits_limit(tmp_path):
    f = make_session_factory(f"sqlite:///{tmp_path}/r.db")
    service = RoomService(f, rng=random.Random(1))
    upd = service.create(1, "Хост")
    for uid in (2, 3):
        service.join_code(uid, f"И{uid}", upd.room.code)
    service.act(1, "ui:themes")
    room = service.get(upd.room_id)
    checked = 0
    for uid in (1, 2, 3):
        for _, action in [b for row in room.view_for(uid, "bot", 0).rows for b in row]:
            if not action.startswith("url:"):
                assert len(f"rm:{action}".encode()) <= 64
                checked += 1
    assert checked > 0
    big = 123456789012
    assert len(f"rm:v:{big}".encode()) <= 64


def test_reply_keyboard_has_all_features():
    labels = [b.text for row in main_reply_keyboard(True).keyboard for b in row]
    assert set(labels) == ALL_BUTTONS
    plain = [b.text for row in main_reply_keyboard(False).keyboard for b in row]
    assert "Админ" not in plain
    for needed in ("Наборы слов", "Профиль", "Рейтинг", "История игр", "Настройки"):
        assert needed in plain
