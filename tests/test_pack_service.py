from tests.pack_fixtures import DEFAULT_PACKS
import random

import pytest
from sqlalchemy import create_engine, inspect, text

from app.models.database.models import GameSession
from app.models.database.pack_repository import PackRepository
from app.models.database.session import make_session_factory
from app.models.services.pack_service import (
    MAX_USER_PACKS,
    MAX_WORD_LEN,
    MIN_WORDS,
    REPORT_THRESHOLD,
    PackError,
    PackService,
    split_words,
)
from app.models.services.room_service import RoomService
from tests.helpers import room_circle, room_guess

WORDS = [f"слово{i}" for i in range(12)]


@pytest.fixture
def env(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path}/p.db")
    repo = PackRepository(factory)
    repo.seed(DEFAULT_PACKS)
    return factory, PackService(factory, repo.load_library())


def make_pack(svc, user=1, title="Мой набор", words=None, public=False):
    pack_id = svc.create_pack(user, "Автор", title, words if words is not None else WORDS)
    if public:
        svc.set_public(user, pack_id, True)
    return pack_id


def test_create_normalizes_and_dedupes_words(env):
    _, svc = env
    pid = make_pack(svc, words=["  Кот ", "кот", "Пёс", "", "x" * (MAX_WORD_LEN + 1)])
    info = svc.info(1, pid)
    assert info.words_count == 2 and not info.is_public and info.owner_name == "Автор"
    assert info.slug == f"u{pid}" and info.category == "Мои наборы"


def test_title_validation_and_pack_limit(env):
    _, svc = env
    with pytest.raises(PackError):
        svc.create_pack(1, "А", "   ")
    with pytest.raises(PackError):
        svc.create_pack(1, "А", "x" * 61)
    for i in range(MAX_USER_PACKS):
        svc.create_pack(1, "А", f"Набор {i}")
    with pytest.raises(PackError):
        svc.create_pack(1, "А", "Лишний")
    svc.create_pack(2, "Б", "Другой пользователь")


def test_add_words_counts_added_and_skipped(env):
    _, svc = env
    pid = make_pack(svc, words=["кот"])
    added, skipped = svc.add_words(1, pid, ["Кот", "пёс", "лиса", "пёс"])
    assert (added, skipped) == (2, 2)
    assert svc.info(1, pid).words_count == 3


def test_only_owner_can_modify(env):
    _, svc = env
    pid = make_pack(svc)
    wid = svc.list_words(1, pid, 0, 5)[0][0][0]
    for action in (
        lambda: svc.add_words(2, pid, ["х"]),
        lambda: svc.rename(2, pid, "Взлом"),
        lambda: svc.delete_pack(2, pid),
        lambda: svc.set_public(2, pid, True),
        lambda: svc.edit_word(2, wid, "новое"),
        lambda: svc.delete_word(2, wid),
        lambda: svc.word(2, wid),
    ):
        with pytest.raises(PackError):
            action()
    assert svc.info(1, pid).title == "Мой набор"


def test_private_pack_invisible_to_others_public_visible(env):
    _, svc = env
    private = make_pack(svc)
    public = make_pack(svc, title="Публичный", public=True)
    with pytest.raises(PackError):
        svc.info(2, private)
    with pytest.raises(PackError):
        svc.list_words(2, private, 0, 5)
    with pytest.raises(PackError):
        svc.export_text(2, private)
    with pytest.raises(PackError):
        svc.toggle_favorite(2, private)
    with pytest.raises(PackError):
        svc.copy_pack(2, "Б", private)
    assert svc.info(2, public).title == "Публичный"
    titles = [i.title for i in svc.list_packs(2, "public")[0]]
    assert titles == ["Публичный"]
    assert svc.list_packs(2, "my")[0] == []


def test_publish_requires_enough_words(env):
    _, svc = env
    pid = make_pack(svc, words=["один", "два"])
    with pytest.raises(PackError):
        svc.set_public(1, pid, True)
    svc.add_words(1, pid, WORDS)
    assert svc.set_public(1, pid, True) is True
    assert svc.set_public(1, pid, False) is False


def test_rename_edit_and_delete_word(env):
    _, svc = env
    pid = make_pack(svc, words=["кот", "пёс"])
    svc.rename(1, pid, "Новое имя")
    assert svc.info(1, pid).title == "Новое имя"
    words = svc.list_words(1, pid, 0, 5)[0]
    cat_id = next(i for i, w in words if w == "кот")
    dog_id = next(i for i, w in words if w == "пёс")
    with pytest.raises(PackError):
        svc.edit_word(1, cat_id, "ПЁС")
    svc.edit_word(1, cat_id, "кошка")
    assert svc.delete_word(1, dog_id) == pid
    assert [w for _, w in svc.list_words(1, pid, 0, 5)[0]] == ["кошка"]


def test_copy_pack_creates_private_copy(env):
    _, svc = env
    public = make_pack(svc, title="Оригинал", public=True)
    copy_id = svc.copy_pack(2, "Другой", public)
    copy = svc.info(2, copy_id)
    assert copy.title == "Копия: Оригинал" and copy.words_count == 12
    assert copy.owner_id == 2 and not copy.is_public
    builtin_copy = svc.copy_pack(2, "Другой", svc.list_packs(2, "builtin")[0][0].id)
    assert svc.info(2, builtin_copy).words_count == 20


def test_delete_pack_cascades(env):
    factory, svc = env
    pid = make_pack(svc, public=True)
    svc.toggle_favorite(2, pid)
    svc.report(3, pid, "spam")
    svc.delete_pack(1, pid)
    with pytest.raises(PackError):
        svc.info(1, pid)
    assert svc.list_packs(2, "fav") == ([], 0)
    assert svc.moderation_list(0, 5) == ([], 0)


def test_favorites_toggle_and_listing(env):
    _, svc = env
    pid = make_pack(svc, public=True)
    assert svc.toggle_favorite(2, pid) is True
    assert svc.info(2, pid).is_favorite
    assert [i.id for i in svc.list_packs(2, "fav")[0]] == [pid]
    assert svc.toggle_favorite(2, pid) is False
    assert svc.list_packs(2, "fav") == ([], 0)
    builtin = svc.list_packs(2, "builtin")[0][0]
    assert svc.toggle_favorite(2, builtin.id) is True


def test_favorite_disappears_when_owner_unpublishes(env):
    _, svc = env
    pid = make_pack(svc, public=True)
    svc.toggle_favorite(2, pid)
    svc.set_public(1, pid, False)
    assert svc.list_packs(2, "fav") == ([], 0)
    assert not svc.library_for(2).has(f"u{pid}")


def test_search_by_title_and_word_and_filters(env):
    _, svc = env
    a = make_pack(svc, title="Космос", words=WORDS + ["ракета"], public=True)
    b = make_pack(svc, title="Кухня", words=WORDS, public=True)
    assert [i.id for i in svc.list_packs(2, "public", query="Космос")[0]] == [a]
    assert [i.id for i in svc.list_packs(2, "public", query="ракета")[0]] == [a]
    assert svc.list_packs(2, "public", query="нет такого")[1] == 0
    assert {i.id for i in svc.list_packs(2, "public")[0]} == {a, b}
    assert svc.list_packs(2, "builtin", query="слон")[0][0].title == "Животные"
    assert svc.list_packs(2, "public", query="%")[1] == 0


def test_sorting_pagination_and_category_filter(env):
    _, svc = env
    small = make_pack(svc, title="Бета", words=["a1"] + WORDS[:3], public=False)
    big = make_pack(svc, title="Альфа", words=WORDS)
    names = [i.title for i in svc.list_packs(1, "my", sort="name")[0]]
    assert names == ["Альфа", "Бета"]
    sizes = [i.id for i in svc.list_packs(1, "my", sort="size")[0]]
    assert sizes == [big, small]
    page1, total = svc.list_packs(1, "my", page=0, size=1, sort="name")
    page2, _ = svc.list_packs(1, "my", page=1, size=1, sort="name")
    assert total == 2 and page1[0].title == "Альфа" and page2[0].title == "Бета"
    category = next(i for i, t in svc.categories() if t == "Еда")
    foods = svc.list_packs(1, "builtin", category_id=category)[0]
    assert [f.title for f in foods] == ["Еда и напитки"]


def test_export_import_roundtrip(env):
    _, svc = env
    pid = make_pack(svc, title="Экспорт", words=["кот", "пёс", "лиса"])
    slug, text_data = svc.export_text(1, pid)
    assert slug == f"u{pid}" and text_data.startswith("# Экспорт")
    new_id = svc.import_text(2, "Другой", text_data)
    info = svc.info(2, new_id)
    assert info.title == "Экспорт" and info.words_count == 3 and info.owner_id == 2


def test_import_formats_and_errors(env):
    _, svc = env
    pid = svc.import_text(1, "А", "сыр, хлеб; масло\nмолоко\nсыр")
    info = svc.info(1, pid)
    assert info.title == "Импортированный набор" and info.words_count == 4
    with pytest.raises(PackError):
        svc.import_text(1, "А", "# Только заголовок\n   \n")
    assert split_words("а, б;в\nг") == ["а", "б", "в", "г"]


def test_reports_hide_pack_after_threshold_and_guards(env):
    _, svc = env
    pid = make_pack(svc, public=True)
    private = make_pack(svc, title="Личный")
    builtin = svc.list_packs(2, "builtin")[0][0].id
    with pytest.raises(PackError):
        svc.report(1, pid, "spam")
    with pytest.raises(PackError):
        svc.report(2, private, "spam")
    with pytest.raises(PackError):
        svc.report(2, builtin, "spam")
    with pytest.raises(PackError):
        svc.report(2, pid, "unknown")
    for reporter in range(2, 2 + REPORT_THRESHOLD):
        hidden = svc.report(reporter, pid, "offensive")
    assert hidden is True
    with pytest.raises(PackError):
        svc.report(2, pid, "spam")
    with pytest.raises(PackError):
        svc.info(9, pid)
    assert svc.list_packs(9, "public") == ([], 0)
    assert svc.info(1, pid).is_hidden
    with pytest.raises(PackError):
        svc.set_public(1, pid, True)


def test_moderation_restore_and_delete(env):
    _, svc = env
    pid = make_pack(svc, public=True)
    for reporter in range(2, 2 + REPORT_THRESHOLD):
        svc.report(reporter, pid, "spam")
    items, total = svc.moderation_list(0, 5)
    assert total == 1 and items[0].reports == REPORT_THRESHOLD
    info, reasons = svc.moderation_info(pid)
    assert reasons == ["Спам"] * REPORT_THRESHOLD
    svc.moderate(pid, "restore")
    assert not svc.info(1, pid).is_hidden and svc.moderation_list(0, 5) == ([], 0)
    assert svc.info(9, pid).title
    svc.moderate(pid, "delete")
    with pytest.raises(PackError):
        svc.info(1, pid)
    with pytest.raises(PackError):
        svc.moderate(svc.list_packs(1, "builtin")[0][0].id, "delete")


def test_library_for_includes_own_and_favorites_only(env):
    _, svc = env
    own = make_pack(svc, user=1, title="Свой")
    other_public = make_pack(svc, user=2, title="Чужой публичный", public=True)
    other_private = make_pack(svc, user=2, title="Чужой личный")
    svc.toggle_favorite(1, other_public)
    library = svc.library_for(1)
    assert library.has(f"u{own}") and library.has(f"u{other_public}")
    assert not library.has(f"u{other_private}")
    assert library.has("animals")
    assert not svc.library_for(3).has(f"u{own}")
    empty = svc.create_pack(1, "А", "Пустой", [])
    assert not svc.library_for(1).has(f"u{empty}")


def test_custom_pack_used_in_online_game(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path}/g.db")
    repo = PackRepository(factory)
    repo.seed(DEFAULT_PACKS)
    packs = PackService(factory, repo.load_library())
    custom = packs.create_pack(1, "Хост", "Мой набор", WORDS)
    service = RoomService(
        factory,
        library=repo.load_library(),
        rng=random.Random(1),
        pack_service=packs,
    )
    upd = service.create(1, "Хост")
    service.join_code(2, "Б", upd.room.code)
    service.join_code(3, "В", upd.room.code)
    service.act(1, "ui:settings")
    service.act(1, "ui:themes")
    service.act(1, f"th:u{custom}")
    with pytest.raises(Exception):
        service.act(1, "th:u9999")
    service.act(2, "rd:1")
    service.act(3, "rd:1")
    service.act(1, "start")
    room = service.get(upd.room_id)
    assert room.round.word in WORDS and room.settings["themes"] == [f"u{custom}"]
    assert "Мой набор" in room.view_for(1, "bot", 0).text or room.themes_label() == "Мой набор"
    for uid in (1, 2, 3):
        service.act(uid, "role")
    spy = int(service.get(upd.room_id).round.spy_id)
    room_guess(service, upd.room_id, spy, correct=True)
    assert service.get(upd.room_id).phase == "result"


def test_other_users_private_pack_cannot_be_selected_as_theme(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path}/g2.db")
    repo = PackRepository(factory)
    repo.seed(DEFAULT_PACKS)
    packs = PackService(factory, repo.load_library())
    secret = packs.create_pack(2, "Чужой", "Секрет", WORDS)
    service = RoomService(factory, library=repo.load_library(), pack_service=packs)
    upd = service.create(1, "Хост")
    with pytest.raises(Exception):
        service.act(1, f"th:u{secret}")


def test_schema_migration_adds_missing_columns(tmp_path):
    url = f"sqlite:///{tmp_path}/old.db"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE question_packs (id INTEGER PRIMARY KEY, slug VARCHAR(32), "
                "title VARCHAR(100), description VARCHAR(255), category_id INTEGER, "
                "owner_id BIGINT, is_public BOOLEAN, position INTEGER, created_at DATETIME, updated_at DATETIME)"
            )
        )
    make_session_factory(url)
    columns = {c["name"] for c in inspect(create_engine(url)).get_columns("question_packs")}
    assert {"is_hidden", "owner_name"} <= columns
    make_session_factory(url)


def test_constants_sane():
    assert MIN_WORDS == 10 and REPORT_THRESHOLD == 3
    assert GameSession is not None


def test_yo_and_ye_are_treated_as_same_word(env):
    _, svc = env
    pid = make_pack(svc, words=["пёс"])
    assert svc.add_words(1, pid, ["пес", "ПЁС"]) == (0, 2)
    assert svc.info(1, pid).words_count == 1
    wid = svc.list_words(1, pid, 0, 5)[0][0][0]
    svc.add_words(1, pid, ["лиса"])
    with pytest.raises(PackError):
        svc.edit_word(1, wid, "ЛИСА")
