import re
import time
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    PackReport,
    PackWord,
    QuestionPack,
    RoomMember,
    RoundPlayer,
    SessionPlayer,
    UserPenalty,
)
from app.game.packs import PackLibrary
from app.services.pack_service import PackError, PackService
from app.services.penalty import PERMANENT_UNTIL, describe_ban

MAX_BAN_MINUTES = 3650 * 24 * 60
PERMANENT_WORDS = {"навсегда", "forever", "perm", "permanent"}
UNITS = {
    "": 1, "м": 1, "мин": 1, "m": 1, "min": 1,
    "ч": 60, "час": 60, "h": 60,
    "д": 1440, "дн": 1440, "день": 1440, "d": 1440,
}


class AdminError(Exception):
    pass


def parse_duration(text: str) -> int | None:
    value = (text or "").strip().lower()
    if value in PERMANENT_WORDS:
        return None
    match = re.fullmatch(r"(\d+)\s*([a-zа-я]*)", value)
    if not match or match.group(2) not in UNITS:
        raise ValueError("Срок задаётся так: 30 (минут), 2ч, 7д или навсегда")
    minutes = int(match.group(1)) * UNITS[match.group(2)]
    if minutes <= 0:
        raise ValueError("Срок должен быть больше нуля")
    if minutes > MAX_BAN_MINUTES:
        raise ValueError("Слишком большой срок, используйте «навсегда»")
    return minutes


@dataclass
class BanRow:
    user_id: int
    name: str
    strikes: int
    seconds_left: float
    reason: str

    @property
    def left_text(self) -> str:
        return describe_ban(self.seconds_left)

    @property
    def reason_text(self) -> str:
        return "администратор" if self.reason == "admin" else "выход из игры"


@dataclass
class AdminPack:
    id: int
    slug: str
    title: str
    owner_id: int | None
    owner_name: str | None
    is_public: bool
    is_hidden: bool
    words: int
    reports: int

    def line(self) -> str:
        access = "скрыт" if self.is_hidden else ("публичный" if self.is_public else "личный")
        owner = self.owner_name or self.owner_id
        return f"{self.id}: {self.title} (автор {owner}, {access}, слов {self.words}, жалоб {self.reports})"


class AdminService:
    def __init__(self, factory: sessionmaker, clock=time.time):
        self._factory = factory
        self._clock = clock

    def _name_of(self, db, user_id: int) -> str:
        row = db.execute(
            select(SessionPlayer.name)
            .where(SessionPlayer.user_id == user_id)
            .order_by(SessionPlayer.session_id.desc())
            .limit(1)
        ).first()
        return row[0] if row else "неизвестно"

    def ban(self, user_id: int, minutes: int | None) -> float:
        if user_id <= 0:
            raise AdminError("Некорректный Telegram ID")
        until = PERMANENT_UNTIL if minutes is None else self._clock() + minutes * 60
        with self._factory.begin() as db:
            row = db.get(UserPenalty, user_id)
            if row is None:
                row = UserPenalty(user_id=user_id, strikes=0, last_leave_at=0.0)
                db.add(row)
            row.banned_until = until
            row.reason = "admin"
        return until

    def unban(self, user_id: int, reset_strikes: bool = False) -> bool:
        with self._factory.begin() as db:
            row = db.get(UserPenalty, user_id)
            if row is None:
                return False
            had = row.banned_until > self._clock() or (reset_strikes and row.strikes > 0)
            row.banned_until = 0.0
            row.reason = "leave"
            if reset_strikes:
                row.strikes = 0
                row.last_leave_at = 0.0
            return had

    def list_bans(self) -> list[BanRow]:
        now = self._clock()
        with self._factory() as db:
            rows = db.execute(
                select(UserPenalty)
                .where(UserPenalty.banned_until > now)
                .order_by(UserPenalty.banned_until.desc())
            ).scalars()
            return [
                BanRow(
                    user_id=r.user_id,
                    name=self._name_of(db, r.user_id),
                    strikes=r.strikes,
                    seconds_left=r.banned_until - now,
                    reason=r.reason or "leave",
                )
                for r in rows
            ]

    def user_info(self, user_id: int) -> dict:
        now = self._clock()
        with self._factory() as db:
            row = db.get(UserPenalty, user_id)
            rounds = db.execute(
                select(func.count()).select_from(RoundPlayer).where(RoundPlayer.user_id == user_id)
            ).scalar_one()
            packs = db.execute(
                select(func.count()).select_from(QuestionPack).where(QuestionPack.owner_id == user_id)
            ).scalar_one()
            in_room = db.get(RoomMember, user_id) is not None
            left = max(0.0, (row.banned_until - now)) if row else 0.0
            return {
                "user_id": user_id,
                "name": self._name_of(db, user_id),
                "strikes": row.strikes if row else 0,
                "ban_seconds": left,
                "ban_reason": (row.reason if row else "leave") or "leave",
                "rounds": rounds,
                "packs": packs,
                "in_room": in_room,
            }

    def _pack_row(self, db, pack: QuestionPack) -> AdminPack:
        words = db.execute(
            select(func.count()).select_from(PackWord).where(PackWord.pack_id == pack.id)
        ).scalar_one()
        reports = db.execute(
            select(func.count()).select_from(PackReport).where(PackReport.pack_id == pack.id)
        ).scalar_one()
        return AdminPack(
            id=pack.id,
            slug=pack.slug,
            title=pack.title,
            owner_id=pack.owner_id,
            owner_name=pack.owner_name,
            is_public=bool(pack.is_public),
            is_hidden=bool(pack.is_hidden),
            words=words,
            reports=reports,
        )

    def list_packs(
        self, query: str | None = None, limit: int = 20, offset: int = 0
    ) -> tuple[list[AdminPack], int]:
        conds = [QuestionPack.owner_id.is_not(None)]
        term = " ".join((query or "").split())
        if term:
            options = [
                QuestionPack.title.contains(term, autoescape=True),
                QuestionPack.owner_name.contains(term, autoescape=True),
                QuestionPack.slug == term,
            ]
            if term.isdigit():
                options += [QuestionPack.id == int(term), QuestionPack.owner_id == int(term)]
            conds.append(or_(*options))
        with self._factory() as db:
            total = db.execute(
                select(func.count()).select_from(QuestionPack).where(*conds)
            ).scalar_one()
            packs = (
                db.execute(
                    select(QuestionPack)
                    .where(*conds)
                    .order_by(QuestionPack.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
                .scalars()
                .all()
            )
            return [self._pack_row(db, p) for p in packs], total

    def get_pack(self, pack_id: int) -> AdminPack:
        with self._factory() as db:
            pack = db.get(QuestionPack, pack_id)
            if pack is None:
                raise AdminError("Набор с таким номером не найден")
            if pack.owner_id is None:
                raise AdminError("Готовые наборы хранятся в базе данных и редактируются напрямую в MySQL")
            return self._pack_row(db, pack)

    def _moderator(self) -> PackService:
        return PackService(self._factory, PackLibrary([]))

    def delete_pack(self, pack_id: int) -> AdminPack:
        pack = self.get_pack(pack_id)
        try:
            self._moderator().moderate(pack_id, "delete")
        except PackError as err:
            raise AdminError(str(err)) from err
        return pack

    def set_hidden(self, pack_id: int, hidden: bool) -> AdminPack:
        self.get_pack(pack_id)
        try:
            self._moderator().moderate(pack_id, "hide" if hidden else "restore")
        except PackError as err:
            raise AdminError(str(err)) from err
        return self.get_pack(pack_id)

    def reported_packs(self) -> list[AdminPack]:
        with self._factory() as db:
            reported = select(PackReport.pack_id)
            packs = (
                db.execute(
                    select(QuestionPack)
                    .where(
                        QuestionPack.owner_id.is_not(None),
                        or_(QuestionPack.is_hidden.is_(True), QuestionPack.id.in_(reported)),
                    )
                    .order_by(QuestionPack.id.desc())
                )
                .scalars()
                .all()
            )
            return [self._pack_row(db, p) for p in packs]
