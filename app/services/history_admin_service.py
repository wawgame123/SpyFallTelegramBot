import json
from sqlalchemy import delete, select

from app.database.models import PackWord, QuestionPack, RoomRecord
from app.services.admin_service import AdminError
from app.services.pack_service import PackService, PackError, MIN_WORDS, MAX_WORDS, split_words
from app.game.packs import PackLibrary


class HistoryAdminService:
    """Privileged operations; handlers must check the administrator on each request."""
    def __init__(self, factory):
        self.factory = factory
        self.packs = PackService(factory, PackLibrary([]))

    def pack(self, pack_id):
        with self.factory() as db:
            p = db.get(QuestionPack, pack_id)
            if p is None:
                raise AdminError("Набор уже удалён. Запись о нём сохранена в истории.")
            words = db.execute(select(PackWord.word).where(PackWord.pack_id == pack_id).order_by(PackWord.id)).scalars().all()
            return {"id": p.id, "title": p.title, "public": p.is_public, "hidden": p.is_hidden, "owner": p.owner_id, "words": words}

    def update(self, pack_id, action, value=None):
        with self.factory.begin() as db:
            p = db.execute(select(QuestionPack).where(QuestionPack.id == pack_id).with_for_update()).scalar_one_or_none()
            if p is None:
                raise AdminError("Набор уже удалён")
            if action == "rename":
                p.title = self.packs._check_title(value)
            elif action == "words":
                words, skipped = self.packs._normalize_words(split_words(value))
                if skipped or not words or len(words) > MAX_WORDS:
                    raise AdminError(f"Введите от 1 до {MAX_WORDS} разных слов, каждое не длиннее 40 символов, без повторов.")
                if p.is_public and len(words) < MIN_WORDS:
                    raise AdminError(f"В публичном наборе должно быть не менее {MIN_WORDS} слов. Сначала сделайте его приватным.")
                db.execute(delete(PackWord).where(PackWord.pack_id == pack_id))
                db.add_all(PackWord(pack_id=pack_id, word=w) for w in words)
            elif action == "public":
                if p.is_hidden:
                    raise AdminError("Сначала снимите блокировку набора")
                count = len(db.execute(select(PackWord.id).where(PackWord.pack_id == pack_id)).all())
                if count < MIN_WORDS:
                    raise AdminError(f"Для публикации нужно не менее {MIN_WORDS} слов")
                p.is_public = True
            elif action == "private":
                p.is_public = False
            elif action == "hide":
                p.is_hidden = True
                p.is_public = False
            elif action == "restore":
                p.is_hidden = False
            elif action == "delete":
                for rec in db.execute(select(RoomRecord)).scalars():
                    state = json.loads(rec.state)
                    if state.get("phase") != "final" and p.slug in state.get("settings", {}).get("themes", []):
                        raise AdminError("Набор выбран в действующей комнате. Заблокируйте его; удалить можно после завершения комнаты или смены тем.")
                self.packs._purge(db, pack_id)
            else:
                raise AdminError("Неизвестное действие")
