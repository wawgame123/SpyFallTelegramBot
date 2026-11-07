import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    PackCategory,
    PackReport,
    PackWord,
    QuestionPack,
    UserFavorite,
)
from app.game.packs import Pack, PackLibrary

MIN_WORDS = 10
MAX_WORDS = 500
MAX_WORD_LEN = 40
MAX_TITLE_LEN = 60
MAX_USER_PACKS = 20
REPORT_THRESHOLD = 3
MY_CATEGORY = "Мои наборы"
SCOPES = ("my", "builtin", "fav", "public")
SORTS = ("new", "name", "size")
REPORT_REASONS = {
    "spam": "Спам",
    "offensive": "Оскорбительное содержимое",
    "errors": "Ошибки или мусор",
    "other": "Другое",
}
WORD_SPLIT = re.compile(r"[\n;,]+")


class PackError(Exception):
    pass


@dataclass
class PackInfo:
    id: int
    slug: str
    title: str
    description: str
    category: str
    owner_id: int | None
    owner_name: str | None
    is_public: bool
    is_hidden: bool
    words_count: int
    is_favorite: bool
    reports: int = 0

    @property
    def is_builtin(self) -> bool:
        return self.owner_id is None


def clean_text(raw: str) -> str:
    return " ".join((raw or "").split())


def word_key(word: str) -> str:
    return word.casefold().replace("ё", "е")


def split_words(text: str) -> list[str]:
    return [clean_text(part) for part in WORD_SPLIT.split(text or "") if clean_text(part)]


class PackService:
    def __init__(self, factory: sessionmaker, base_library: PackLibrary):
        self._factory = factory
        self._base = base_library

    def _visible_clause(self, user_id: int):
        return or_(
            QuestionPack.owner_id.is_(None),
            QuestionPack.owner_id == user_id,
            and_(QuestionPack.is_public.is_(True), QuestionPack.is_hidden.is_(False)),
        )

    def _pack(self, db, pack_id: int, user_id: int | None = None, owned: bool = False, lock: bool = False) -> QuestionPack:
        pack = db.execute(select(QuestionPack).where(QuestionPack.id == pack_id).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none() if lock else db.get(QuestionPack, pack_id)
        if pack is None:
            raise PackError("Набор не найден")
        if owned:
            if pack.owner_id is None or pack.owner_id != user_id:
                raise PackError("Это действие доступно только автору набора")
        elif user_id is not None:
            visible = (
                pack.owner_id is None
                or pack.owner_id == user_id
                or (pack.is_public and not pack.is_hidden)
            )
            if not visible:
                raise PackError("Набор недоступен")
        return pack

    def _info(self, db, pack: QuestionPack, user_id: int | None) -> PackInfo:
        count = db.execute(
            select(func.count()).select_from(PackWord).where(PackWord.pack_id == pack.id)
        ).scalar_one()
        category = db.get(PackCategory, pack.category_id)
        favorite = False
        if user_id is not None:
            favorite = db.get(UserFavorite, (user_id, pack.id)) is not None
        reports = db.execute(
            select(func.count()).select_from(PackReport).where(PackReport.pack_id == pack.id)
        ).scalar_one()
        return PackInfo(
            id=pack.id,
            slug=pack.slug,
            title=pack.title,
            description=pack.description or "",
            category=category.title if category else "",
            owner_id=pack.owner_id,
            owner_name=pack.owner_name,
            is_public=bool(pack.is_public),
            is_hidden=bool(pack.is_hidden),
            words_count=count,
            is_favorite=favorite,
            reports=reports,
        )

    def info(self, user_id: int, pack_id: int) -> PackInfo:
        with self._factory() as db:
            return self._info(db, self._pack(db, pack_id, user_id), user_id)

    def categories(self) -> list[tuple[int, str]]:
        with self._factory() as db:
            rows = db.execute(select(PackCategory.id, PackCategory.title).order_by(PackCategory.title))
            return [(row[0], row[1]) for row in rows]

    def list_packs(
        self,
        user_id: int,
        scope: str,
        page: int = 0,
        size: int = 6,
        query: str | None = None,
        sort: str = "new",
        category_id: int | None = None,
    ) -> tuple[list[PackInfo], int]:
        if scope not in SCOPES:
            raise PackError("Неизвестный раздел")
        if sort not in SORTS:
            sort = "new"
        conds = []
        if scope == "my":
            conds.append(QuestionPack.owner_id == user_id)
        elif scope == "builtin":
            conds += [QuestionPack.owner_id.is_(None), QuestionPack.is_public.is_(True), QuestionPack.is_hidden.is_(False)]
        elif scope == "public":
            conds += [
                QuestionPack.owner_id.is_not(None),
                QuestionPack.is_public.is_(True),
                QuestionPack.is_hidden.is_(False),
            ]
        else:
            conds += [
                QuestionPack.id.in_(select(UserFavorite.pack_id).where(UserFavorite.user_id == user_id)),
                self._visible_clause(user_id),
            ]
        if category_id:
            conds.append(QuestionPack.category_id == category_id)
        if query:
            term = clean_text(query)
            conds.append(
                or_(
                    QuestionPack.title.contains(term, autoescape=True),
                    QuestionPack.id.in_(
                        select(PackWord.pack_id).where(PackWord.word.contains(term, autoescape=True))
                    ),
                )
            )
        size_expr = (
            select(func.count()).where(PackWord.pack_id == QuestionPack.id).scalar_subquery()
        )
        if sort == "name":
            order = [QuestionPack.title, QuestionPack.id]
        elif sort == "size":
            order = [size_expr.desc(), QuestionPack.id]
        elif scope == "builtin":
            order = [QuestionPack.position, QuestionPack.id]
        else:
            order = [QuestionPack.updated_at.desc(), QuestionPack.id.desc()]
        with self._factory() as db:
            total = db.execute(
                select(func.count()).select_from(QuestionPack).where(*conds)
            ).scalar_one()
            packs = (
                db.execute(
                    select(QuestionPack)
                    .where(*conds)
                    .order_by(*order)
                    .limit(size)
                    .offset(page * size)
                )
                .scalars()
                .all()
            )
            return [self._info(db, p, user_id) for p in packs], total

    def _normalize_words(self, raw_words: list[str]) -> tuple[list[str], int]:
        words, skipped, seen = [], 0, set()
        for raw in raw_words:
            word = clean_text(raw)
            if not word or len(word) > MAX_WORD_LEN or word_key(word) in seen:
                skipped += 1
                continue
            seen.add(word_key(word))
            words.append(word)
        return words, skipped

    def _check_title(self, title: str) -> str:
        cleaned = clean_text(title)
        if not cleaned:
            raise PackError("Название не может быть пустым")
        if len(cleaned) > MAX_TITLE_LEN:
            raise PackError(f"Название длиннее {MAX_TITLE_LEN} символов")
        return cleaned

    def _my_category(self, db) -> PackCategory:
        category = db.execute(
            select(PackCategory).where(PackCategory.title == MY_CATEGORY)
        ).scalar_one_or_none()
        if category is None:
            category = PackCategory(title=MY_CATEGORY)
            db.add(category)
            db.flush()
        return category

    def _new_pack(
        self, db, user_id: int, owner_name: str, title: str, description: str, words: list[str]
    ) -> QuestionPack:
        count = db.execute(
            select(func.count()).select_from(QuestionPack).where(QuestionPack.owner_id == user_id)
        ).scalar_one()
        if count >= MAX_USER_PACKS:
            raise PackError(f"Можно создать не более {MAX_USER_PACKS} наборов")
        category = self._my_category(db)
        pack = QuestionPack(
            slug=f"tmp{secrets.token_hex(6)}",
            title=title,
            description=description[:255],
            category_id=category.id,
            owner_id=user_id,
            owner_name=clean_text(owner_name)[:40] or "Игрок",
            is_public=False,
            is_hidden=False,
            position=0,
        )
        db.add(pack)
        db.flush()
        pack.slug = f"u{pack.id}"
        for word in words[:MAX_WORDS]:
            db.add(PackWord(pack_id=pack.id, word=word))
        return pack

    def create_pack(
        self,
        user_id: int,
        owner_name: str,
        title: str,
        words: list[str] | None = None,
        description: str = "",
    ) -> int:
        title = self._check_title(title)
        cleaned, _ = self._normalize_words(words or [])
        with self._factory.begin() as db:
            pack = self._new_pack(db, user_id, owner_name, title, description, cleaned)
            return pack.id

    def add_words(self, user_id: int, pack_id: int, raw_words: list[str]) -> tuple[int, int]:
        words, skipped = self._normalize_words(raw_words)
        with self._factory.begin() as db:
            pack = self._pack(db, pack_id, user_id, owned=True)
            existing = {
                word_key(row[0])
                for row in db.execute(select(PackWord.word).where(PackWord.pack_id == pack.id))
            }
            total = len(existing)
            added = 0
            for word in words:
                if word_key(word) in existing or total >= MAX_WORDS:
                    skipped += 1
                    continue
                db.add(PackWord(pack_id=pack.id, word=word))
                existing.add(word_key(word))
                total += 1
                added += 1
            pack.updated_at = datetime.now(timezone.utc)
            return added, skipped

    def list_words(
        self, user_id: int, pack_id: int, page: int, size: int
    ) -> tuple[list[tuple[int, str]], int]:
        with self._factory() as db:
            self._pack(db, pack_id, user_id)
            total = db.execute(
                select(func.count()).select_from(PackWord).where(PackWord.pack_id == pack_id)
            ).scalar_one()
            rows = db.execute(
                select(PackWord.id, PackWord.word)
                .where(PackWord.pack_id == pack_id)
                .order_by(PackWord.word, PackWord.id)
                .limit(size)
                .offset(page * size)
            )
            return [(row[0], row[1]) for row in rows], total

    def word(self, user_id: int, word_id: int) -> tuple[int, str]:
        with self._factory() as db:
            row = db.get(PackWord, word_id)
            if row is None:
                raise PackError("Слово не найдено")
            self._pack(db, row.pack_id, user_id, owned=True)
            return row.pack_id, row.word

    def edit_word(self, user_id: int, word_id: int, new_word: str) -> int:
        cleaned = clean_text(new_word)
        if not cleaned or len(cleaned) > MAX_WORD_LEN:
            raise PackError(f"Слово должно быть от 1 до {MAX_WORD_LEN} символов")
        with self._factory.begin() as db:
            row = db.get(PackWord, word_id)
            if row is None:
                raise PackError("Слово не найдено")
            self._pack(db, row.pack_id, user_id, owned=True)
            others = {
                word_key(r[0])
                for r in db.execute(
                    select(PackWord.word).where(PackWord.pack_id == row.pack_id, PackWord.id != word_id)
                )
            }
            if word_key(cleaned) in others:
                raise PackError("Такое слово уже есть в наборе")
            row.word = cleaned
            return row.pack_id

    def delete_word(self, user_id: int, word_id: int) -> int:
        with self._factory.begin() as db:
            row = db.get(PackWord, word_id)
            if row is None:
                raise PackError("Слово не найдено")
            pack = self._pack(db, row.pack_id, user_id, owned=True, lock=True)
            count = db.execute(select(func.count()).select_from(PackWord).where(PackWord.pack_id == pack.id)).scalar_one()
            if pack.is_public and count <= MIN_WORDS:
                raise PackError(f"В публичном наборе должно остаться не менее {MIN_WORDS} слов. Сначала сделайте набор личным.")
            pack_id = row.pack_id
            db.delete(row)
            return pack_id

    def rename(self, user_id: int, pack_id: int, title: str) -> None:
        title = self._check_title(title)
        with self._factory.begin() as db:
            self._pack(db, pack_id, user_id, owned=True).title = title

    def set_public(self, user_id: int, pack_id: int, public: bool) -> bool:
        with self._factory.begin() as db:
            pack = self._pack(db, pack_id, user_id, owned=True, lock=True)
            if public:
                if pack.is_hidden:
                    raise PackError("Набор скрыт модерацией и не может быть опубликован")
                count = db.execute(
                    select(func.count()).select_from(PackWord).where(PackWord.pack_id == pack.id)
                ).scalar_one()
                if count < MIN_WORDS:
                    raise PackError(f"Для публикации нужно не менее {MIN_WORDS} слов")
            pack.is_public = public
            return public

    def copy_pack(self, user_id: int, owner_name: str, pack_id: int) -> int:
        with self._factory.begin() as db:
            source = self._pack(db, pack_id, user_id)
            words = [
                row[0]
                for row in db.execute(
                    select(PackWord.word).where(PackWord.pack_id == source.id).order_by(PackWord.id)
                )
            ]
            title = clean_text(f"Копия: {source.title}")[:MAX_TITLE_LEN]
            pack = self._new_pack(db, user_id, owner_name, title, source.description or "", words)
            return pack.id

    def _purge(self, db, pack_id: int) -> None:
        db.execute(delete(PackWord).where(PackWord.pack_id == pack_id))
        db.execute(delete(UserFavorite).where(UserFavorite.pack_id == pack_id))
        db.execute(delete(PackReport).where(PackReport.pack_id == pack_id))
        db.execute(delete(QuestionPack).where(QuestionPack.id == pack_id))

    def delete_pack(self, user_id: int, pack_id: int) -> None:
        with self._factory.begin() as db:
            self._pack(db, pack_id, user_id, owned=True)
            self._purge(db, pack_id)

    def toggle_favorite(self, user_id: int, pack_id: int) -> bool:
        with self._factory.begin() as db:
            self._pack(db, pack_id, user_id)
            row = db.get(UserFavorite, (user_id, pack_id))
            if row:
                db.delete(row)
                return False
            db.add(UserFavorite(user_id=user_id, pack_id=pack_id))
            return True

    def export_text(self, user_id: int, pack_id: int) -> tuple[str, str]:
        with self._factory() as db:
            pack = self._pack(db, pack_id, user_id)
            words = [
                row[0]
                for row in db.execute(
                    select(PackWord.word).where(PackWord.pack_id == pack.id).order_by(PackWord.id)
                )
            ]
            return pack.slug, "\n".join([f"# {pack.title}"] + words) + "\n"

    def import_text(self, user_id: int, owner_name: str, text: str) -> int:
        lines = [line for line in (text or "").splitlines() if line.strip()]
        title = "Импортированный набор"
        if lines and lines[0].lstrip().startswith("#"):
            title = lines.pop(0).lstrip().lstrip("#").strip() or title
        words, _ = self._normalize_words(split_words("\n".join(lines)))
        if not words:
            raise PackError("В файле не найдено ни одного корректного слова")
        return self.create_pack(user_id, owner_name, title, words)

    def report(self, user_id: int, pack_id: int, reason_code: str) -> bool:
        reason = REPORT_REASONS.get(reason_code)
        if reason is None:
            raise PackError("Неизвестная причина жалобы")
        with self._factory.begin() as db:
            pack = self._pack(db, pack_id, user_id)
            if pack.owner_id is None or pack.owner_id == user_id or not pack.is_public:
                raise PackError("Пожаловаться можно только на чужой публичный набор")
            if db.execute(
                select(PackReport.id).where(
                    PackReport.pack_id == pack_id, PackReport.reporter_id == user_id
                )
            ).first():
                raise PackError("Вы уже отправляли жалобу на этот набор")
            db.add(PackReport(pack_id=pack_id, reporter_id=user_id, reason=reason))
            db.flush()
            count = db.execute(
                select(func.count()).select_from(PackReport).where(PackReport.pack_id == pack_id)
            ).scalar_one()
            if count >= REPORT_THRESHOLD:
                pack.is_hidden = True
            return bool(pack.is_hidden)

    def moderation_list(self, page: int, size: int) -> tuple[list[PackInfo], int]:
        reported = select(PackReport.pack_id)
        cond = or_(QuestionPack.is_hidden.is_(True), QuestionPack.id.in_(reported))
        with self._factory() as db:
            total = db.execute(
                select(func.count()).select_from(QuestionPack).where(cond)
            ).scalar_one()
            packs = (
                db.execute(
                    select(QuestionPack)
                    .where(cond)
                    .order_by(QuestionPack.updated_at.desc(), QuestionPack.id.desc())
                    .limit(size)
                    .offset(page * size)
                )
                .scalars()
                .all()
            )
            return [self._info(db, p, None) for p in packs], total

    def moderation_info(self, pack_id: int) -> tuple[PackInfo, list[str]]:
        with self._factory() as db:
            pack = self._pack(db, pack_id)
            reasons = [
                row[0]
                for row in db.execute(
                    select(PackReport.reason).where(PackReport.pack_id == pack_id).order_by(PackReport.id)
                )
            ]
            return self._info(db, pack, None), reasons

    def moderate(self, pack_id: int, action: str) -> None:
        with self._factory.begin() as db:
            pack = self._pack(db, pack_id)
            if pack.owner_id is None:
                raise PackError("Готовые наборы модерации не подлежат")
            if action == "restore":
                pack.is_hidden = False
                db.execute(delete(PackReport).where(PackReport.pack_id == pack_id))
            elif action == "hide":
                pack.is_hidden = True
                pack.is_public = False
            elif action == "delete":
                self._purge(db, pack_id)
            else:
                raise PackError("Неизвестное действие")

    def _to_packs(self, db, packs: list[QuestionPack]) -> list[Pack]:
        if not packs:
            return []
        ids = [p.id for p in packs]
        words: dict[int, list[str]] = {}
        for row in db.execute(
            select(PackWord.pack_id, PackWord.word).where(PackWord.pack_id.in_(ids)).order_by(PackWord.id)
        ):
            words.setdefault(row[0], []).append(row[1])
        categories = {c.id: c.title for c in db.execute(select(PackCategory)).scalars()}
        return [
            Pack(
                id=p.slug,
                title=p.title,
                description=p.description or "",
                category=categories.get(p.category_id, ""),
                words=tuple(words.get(p.id, [])),
            )
            for p in packs
            if words.get(p.id)
        ]

    def library_for(self, user_id: int) -> PackLibrary:
        # Read built-in packs too: admin changes must apply without restarting.
        with self._factory() as db:
            favorites = select(UserFavorite.pack_id).where(UserFavorite.user_id == user_id)
            packs = db.execute(select(QuestionPack).where(
                QuestionPack.is_hidden.is_(False),
                or_(
                    and_(QuestionPack.owner_id.is_(None), QuestionPack.is_public.is_(True)),
                    QuestionPack.owner_id == user_id,
                    and_(QuestionPack.id.in_(favorites), QuestionPack.is_public.is_(True)),
                ),
            ).order_by(QuestionPack.position, QuestionPack.id)).scalars().all()
            return PackLibrary(self._to_packs(db, packs))

    def packs_by_slugs(self, slugs: list[str]) -> list[Pack]:
        if not slugs:
            return []
        with self._factory() as db:
            packs = (
                db.execute(select(QuestionPack).where(QuestionPack.slug.in_(slugs))).scalars().all()
            )
            return self._to_packs(db, packs)
