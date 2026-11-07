from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Double, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LocalGameRecord(Base):
    __tablename__ = "local_games"

    owner_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    state: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT(), "mysql"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class RoomRecord(Base):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(8), unique=True, index=True)
    invite_token: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    host_id: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT(), "mysql"), nullable=False)
    deadline: Mapped[float | None] = mapped_column(Double, nullable=True, index=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class RoomMember(Base):
    __tablename__ = "room_members"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    room_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rooms.id", ondelete="CASCADE"), index=True
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PackCategory(Base):
    __tablename__ = "pack_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(64), unique=True)


class QuestionPack(Base):
    __tablename__ = "question_packs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(255), default="")
    category_id: Mapped[int] = mapped_column(Integer, ForeignKey("pack_categories.id"), index=True)
    owner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    owner_name: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class PackWord(Base):
    __tablename__ = "pack_words"
    __table_args__ = (UniqueConstraint("pack_id", "word", name="uq_pack_word"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pack_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("question_packs.id", ondelete="CASCADE"), index=True
    )
    word: Mapped[str] = mapped_column(String(64))


class GameSession(Base):
    __tablename__ = "game_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    room_code: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(100))
    host_id: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SessionPlayer(Base):
    __tablename__ = "session_players"

    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("game_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, index=True
    )
    name: Mapped[str] = mapped_column(String(40))
    score: Mapped[int] = mapped_column(Integer, default=0)


class MatchRound(Base):
    __tablename__ = "match_rounds"
    __table_args__ = (UniqueConstraint("session_id", "round_no", name="uq_session_round"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("game_sessions.id", ondelete="CASCADE"), index=True
    )
    round_no: Mapped[int] = mapped_column(Integer)
    theme: Mapped[str] = mapped_column(String(100))
    word: Mapped[str] = mapped_column(String(64))
    pack_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pack_title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    spy_user_id: Mapped[int] = mapped_column(BigInteger)
    spy_name: Mapped[str] = mapped_column(String(40))
    outcome: Mapped[str] = mapped_column(String(24))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class RoundPlayer(Base):
    __tablename__ = "round_players"

    round_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("match_rounds.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, index=True
    )
    name: Mapped[str] = mapped_column(String(40))
    role: Mapped[str] = mapped_column(String(8))
    result: Mapped[str] = mapped_column(String(8))
    score_delta: Mapped[int] = mapped_column(Integer, default=0)


class UserFavorite(Base):
    __tablename__ = "user_favorites"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    pack_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("question_packs.id", ondelete="CASCADE"), primary_key=True
    )


class PackReport(Base):
    __tablename__ = "pack_reports"
    __table_args__ = (UniqueConstraint("pack_id", "reporter_id", name="uq_pack_reporter"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pack_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("question_packs.id", ondelete="CASCADE"), index=True
    )
    reporter_id: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UserSetting(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    tz_offset_min: Mapped[int] = mapped_column(Integer, default=0)
    emoji: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    colors: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class UserPenalty(Base):
    __tablename__ = "user_penalties"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    strikes: Mapped[int] = mapped_column(Integer, default=0)
    banned_until: Mapped[float] = mapped_column(Double, default=0.0)
    last_leave_at: Mapped[float] = mapped_column(Double, default=0.0)
    reason: Mapped[str] = mapped_column(String(16), default="leave", server_default="leave")
