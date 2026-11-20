from tests.pack_fixtures import DEFAULT_PACKS, test_library
import random

from sqlalchemy import select

from app.database.models import PackCategory, PackWord, QuestionPack
from app.database.pack_repository import PackRepository
from app.database.session import make_session_factory
from app.game.room import Room
from app.game.session import LocalSession
from app.services.room_service import RoomService


def make_repo(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path}/packs.db")
    return factory, PackRepository(factory)


def test_explicit_import_creates_packs_once(tmp_path):
    factory, repo = make_repo(tmp_path)
    assert repo.seed(DEFAULT_PACKS) == 8
    assert repo.seed(DEFAULT_PACKS) == 0
    with factory() as db:
        assert len(db.execute(select(QuestionPack)).scalars().all()) == 8
        assert len(db.execute(select(PackWord)).scalars().all()) == 160
        assert db.execute(select(PackCategory)).scalars().first() is not None


def test_library_from_db_matches_import_fixture(tmp_path):
    _, repo = make_repo(tmp_path)
    repo.seed(DEFAULT_PACKS)
    from_db = repo.load_library()
    from_fixture = test_library()
    assert [p.id for p in from_db.packs()] == [p.id for p in from_fixture.packs()]
    for pack in from_fixture.packs():
        db_pack = from_db.get(pack.id)
        assert db_pack.words == pack.words
        assert db_pack.title == pack.title and db_pack.category == pack.category


def test_new_theme_in_db_needs_no_engine_change(tmp_path):
    factory, repo = make_repo(tmp_path)
    repo.seed(DEFAULT_PACKS)
    with factory.begin() as db:
        cat = db.execute(select(PackCategory)).scalars().first()
        pack = QuestionPack(
            slug="space", title="🚀 Космос", description="", category_id=cat.id, position=99
        )
        db.add(pack)
        db.flush()
        for word in ["ракета", "комета", "галактика", "орбита"]:
            db.add(PackWord(pack_id=pack.id, word=word))
    library = repo.load_library()
    assert library.has("space")
    session = LocalSession(library=library, rng=random.Random(1))
    session.act(session.seq, "n:3")
    for name in ["А", "Б", "В"]:
        session.handle_text(name)
    session.act(session.seq, "th")
    session.act(session.seq, "t:space")
    session.act(session.seq, "ok")
    session.act(session.seq, "go")
    assert session.round.word in {"ракета", "комета", "галактика", "орбита"}


def test_user_packs_are_not_in_builtin_library(tmp_path):
    factory, repo = make_repo(tmp_path)
    repo.seed(DEFAULT_PACKS)
    with factory.begin() as db:
        cat = db.execute(select(PackCategory)).scalars().first()
        pack = QuestionPack(
            slug="mine", title="Мой", description="", category_id=cat.id, owner_id=42
        )
        db.add(pack)
        db.flush()
        db.add(PackWord(pack_id=pack.id, word="секрет"))
    assert not repo.load_library().has("mine")


def test_room_service_uses_db_library(tmp_path):
    factory, repo = make_repo(tmp_path)
    repo.seed(DEFAULT_PACKS)
    service = RoomService(factory, library=repo.load_library(), rng=random.Random(2))
    upd = service.create(1, "Хост")
    service.join_code(2, "Б", upd.room.code)
    service.join_code(3, "В", upd.room.code)
    service.act(1, "ui:settings")
    service.act(1, "th:food")
    service.act(2, "rd:1")
    service.act(3, "rd:1")
    service.act(1, "start")
    room = service.get(upd.room_id)
    assert room.round.word in repo.load_library().get("food").words
    assert isinstance(room, Room)
