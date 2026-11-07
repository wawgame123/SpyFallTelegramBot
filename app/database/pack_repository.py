from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.database.models import PackCategory, PackWord, QuestionPack
from app.game.packs import Pack, PackLibrary


class PackRepository:
    def __init__(self, factory: sessionmaker):
        self._factory = factory

    def seed(self, packs: list[dict]) -> int:
        """Explicitly import packs; never called automatically at application startup."""
        created = 0
        with self._factory.begin() as db:
            for position, item in enumerate(packs):
                category = db.execute(
                    select(PackCategory).where(PackCategory.title == item["category"])
                ).scalar_one_or_none()
                if category is None:
                    category = PackCategory(title=item["category"])
                    db.add(category)
                    db.flush()
                pack = db.execute(
                    select(QuestionPack).where(QuestionPack.slug == item["id"])
                ).scalar_one_or_none()
                if pack is None:
                    pack = QuestionPack(
                        slug=item["id"],
                        title=item["title"],
                        description=item["description"],
                        category_id=category.id,
                        owner_id=None,
                        is_public=True,
                        position=position,
                    )
                    db.add(pack)
                    db.flush()
                    created += 1
                elif pack.owner_id is None:
                    pack.title = item["title"]
                    pack.description = item["description"]
                    pack.category_id = category.id
                    pack.position = position
                existing = {
                    row[0]
                    for row in db.execute(select(PackWord.word).where(PackWord.pack_id == pack.id))
                }
                for word in item["words"]:
                    if word not in existing:
                        db.add(PackWord(pack_id=pack.id, word=word))
                        existing.add(word)
        return created

    def load_library(self) -> PackLibrary:
        with self._factory() as db:
            categories = {c.id: c.title for c in db.execute(select(PackCategory)).scalars()}
            packs = db.execute(
                select(QuestionPack)
                .where(QuestionPack.owner_id.is_(None))
                .order_by(QuestionPack.position, QuestionPack.id)
            ).scalars().all()
            words: dict[int, list[str]] = {}
            for row in db.execute(select(PackWord.pack_id, PackWord.word).order_by(PackWord.id)):
                words.setdefault(row[0], []).append(row[1])
            return PackLibrary(
                [
                    Pack(
                        id=p.slug,
                        title=p.title,
                        description=p.description,
                        category=categories.get(p.category_id, ""),
                        words=tuple(words.get(p.id, [])),
                    )
                    for p in packs
                    if words.get(p.id)
                ]
            )
