import json
import random
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database.models import (
    GameSession,
    MatchRound,
    QuestionPack,
    RoomMember,
    RoomRecord,
    RoundPlayer,
    SessionPlayer,
    UserPenalty,
)
from app.game.engine import Phase
from app.services.limits import FailedJoinLimiter
from app.game.packs import PackLibrary, load_default_library
from app.game.room import Room, RoomError, new_code
from app.services.penalty import (
    ban_message,
    format_duration,
    next_penalty,
)


@dataclass
class Update:
    room_id: int
    room: Room | None
    alert: str | None = None
    changed: bool = True
    left_user: int | None = None
    penalty: tuple[int, int] | None = None


class RoomService:
    def __init__(
        self,
        factory: sessionmaker,
        library: PackLibrary | None = None,
        rng: random.Random | None = None,
        clock=time.time,
        pack_service=None,
    ):
        self._factory = factory
        self._library = library or load_default_library()
        self._rng = rng
        self._clock = clock
        self._packs = pack_service
        self.join_limiter = FailedJoinLimiter(clock)

    def _host_library(self, host_id: int, themes: list[str] | None = None):
        if self._packs is None:
            return self._library
        library = self._packs.library_for(host_id)
        missing = [t for t in (themes or []) if not library.has(t)]
        if missing:
            library = library.with_packs(self._packs.packs_by_slugs(missing))
        return library

    def _room(self, rec: RoomRecord) -> Room:
        state = json.loads(rec.state)
        themes = state.get("settings", {}).get("themes")
        if self._packs is not None and state.get("phase") != "lobby" and themes:
            # A running round already carries its own words and roles. Only
            # selected packs are needed to restore its views; theme selection
            # in the lobby still loads the full, current library.
            library = PackLibrary(self._packs.packs_by_slugs(themes))
        else:
            library = self._host_library(rec.host_id, themes)
        return Room.from_dict(state, library, self._rng)

    def _activity_time(self):
        return datetime.fromtimestamp(self._clock(), timezone.utc)

    def _store(self, rec: RoomRecord, room: Room, *, player_activity=False) -> None:
        room.prune_missing_themes()
        rec.state = json.dumps(room.to_dict(), ensure_ascii=False)
        rec.host_id = room.host_id
        active = (
            room.phase == "playing"
            and room.round is not None
            and room.round.phase in (Phase.DISCUSSION, Phase.LAST_CHANCE)
        )
        rec.deadline = room.deadline if active else None
        rec.updated_at = datetime.now(timezone.utc)
        if player_activity:
            rec.last_activity_at = self._activity_time()

    def _lock(self, db, room_id: int) -> RoomRecord | None:
        stmt = select(RoomRecord).where(RoomRecord.id == room_id).with_for_update().execution_options(populate_existing=True)
        return db.execute(stmt).scalar_one_or_none()

    def panel_id_of(self, user_id: int) -> int | None:
        """Read the panel identity without reconstructing a game or its packs."""
        with self._factory() as db:
            state = db.execute(
                select(RoomRecord.state)
                .join(RoomMember, RoomMember.room_id == RoomRecord.id)
                .where(RoomMember.user_id == user_id)
            ).scalar_one_or_none()
            if state is None:
                return None
            player = next((p for p in json.loads(state).get("players", []) if p["id"] == user_id), None)
            return player.get("panel") if player else None

    def _record_history(self, db, rec: RoomRecord, room: Room) -> None:
        if room.phase != "result" or not room.history:
            return
        last = room.history[-1]
        if room.session_id is None:
            session = GameSession(
                room_id=rec.id,
                room_code=room.code,
                title=room.title,
                host_id=room.host_id,
                status="active",
                is_test=room.is_test,
                started_at=rec.created_at,
            )
            db.add(session)
            db.flush()
            room.session_id = session.id
        known = db.execute(
            select(MatchRound.id).where(
                MatchRound.session_id == room.session_id, MatchRound.round_no == last["round"]
            )
        ).first()
        if known:
            return
        spy_id = int(last["spy"])
        names = last.get("names") or {str(p["id"]): p["name"] for p in room.players}
        pack = db.execute(select(QuestionPack).where(QuestionPack.slug == room.round.theme_id)).scalar_one_or_none() if room.round else None
        match = MatchRound(
            session_id=room.session_id,
            round_no=last["round"],
            theme=room.round.theme if room.round else "",
            word=last["word"],
            pack_id=pack.id if pack else None,
            pack_title=room.round.theme if room.round else None,
            spy_user_id=spy_id,
            spy_name=names.get(last["spy"], "?"),
            outcome=last["outcome"],
            finished_at=datetime.now(timezone.utc),
        )
        db.add(match)
        db.flush()
        for pid, delta in last["deltas"].items():
            uid = int(pid)
            player_name = names.get(pid, "Игрок")
            role = "spy" if uid == spy_id else "civilian"
            if last["outcome"] == "draw":
                result = "draw"
            elif last["outcome"] in ("civilians_win", "spy_wrong_guess", "spy_left"):
                result = "loss" if role == "spy" else "win"
            else:
                result = "win" if role == "spy" else "loss"
            db.add(
                RoundPlayer(
                    round_id=match.id,
                    user_id=uid,
                    name=player_name,
                    role=role,
                    result=result,
                    score_delta=delta,
                )
            )
            total = room.scores.get(pid, 0)
            row = db.get(SessionPlayer, (room.session_id, uid))
            if row:
                row.name = player_name
                row.score = total
            else:
                db.add(
                    SessionPlayer(
                        session_id=room.session_id, user_id=uid, name=player_name, score=total
                    )
                )

    def _end_session(self, db, session_id: int | None, status: str) -> None:
        if session_id is None:
            return
        db.execute(
            update(GameSession)
            .where(GameSession.id == session_id, GameSession.status == "active")
            .values(status=status, ended_at=datetime.now(timezone.utc))
        )

    def ban_seconds(self, user_id: int) -> float:
        return self.ban_status(user_id)[0]

    def ban_status(self, user_id: int) -> tuple[float, str]:
        with self._factory() as db:
            row = db.get(UserPenalty, user_id)
            if row is None:
                return 0.0, "leave"
            return max(0.0, row.banned_until - self._clock()), row.reason or "leave"

    def _check_ban(self, db, user_id: int) -> None:
        row = db.get(UserPenalty, user_id)
        if row is not None:
            left = row.banned_until - self._clock()
            if left > 0:
                raise RoomError(ban_message(left, row.reason or "leave"))

    def _penalty_preview(self, db, user_id: int, now: float) -> tuple[int, int]:
        row = db.get(UserPenalty, user_id)
        return next_penalty(row.strikes if row else 0, row.last_leave_at if row else 0.0, now)

    def _apply_penalty(self, db, user_id: int, now: float) -> tuple[int, int]:
        strikes, minutes = self._penalty_preview(db, user_id, now)
        row = db.get(UserPenalty, user_id)
        if row is None:
            row = UserPenalty(user_id=user_id)
            db.add(row)
        row.strikes = strikes
        until = now + minutes * 60
        if until >= (row.banned_until or 0.0):
            row.banned_until = until
            row.reason = "leave"
        row.last_leave_at = now
        return strikes, minutes

    def room_id_of(self, user_id: int) -> int | None:
        with self._factory() as db:
            member = db.get(RoomMember, user_id)
            return member.room_id if member else None

    def get(self, room_id: int) -> Room | None:
        with self._factory.begin() as db:
            rec = db.get(RoomRecord, room_id)
            if rec is None:
                return None
            original_themes = json.loads(rec.state).get("settings", {}).get("themes", [])
            room = self._room(rec)
            if room.phase == "lobby" and original_themes != room.settings["themes"]:
                rec = self._lock(db, room_id)
                if rec is None:
                    return None
                room = self._room(rec)
                self._store(rec, room)
            return room

    def create(self, user_id: int, name: str, test_size: int | None = None) -> Update:
        if test_size not in (None, 1, 2):
            raise RoomError("Тестовое лобби возможно только на 1 или 2 игроков")
        with self._factory.begin() as db:
            if db.get(RoomMember, user_id):
                raise RoomError("Вы уже находитесь в комнате")
            if test_size is None:
                self._check_ban(db, user_id)
            code = new_code()
            while db.execute(select(RoomRecord.id).where(RoomRecord.code == code)).first():
                code = new_code()
            token = secrets.token_urlsafe(9)
            room = Room(code, token, user_id, "", self._host_library(user_id), self._rng)
            room.join(user_id, name)
            room.title = f"Комната {room.players[0]['name']}"
            if test_size:
                room.enable_test_mode(test_size, user_id)
            rec = RoomRecord(code=code, invite_token=token, host_id=user_id, state="")
            db.add(rec)
            db.flush()
            self._store(rec, room, player_activity=True)
            db.add(RoomMember(user_id=user_id, room_id=rec.id))
            return Update(room_id=rec.id, room=room)

    def _join(self, db, rec: RoomRecord | None, user_id: int, name: str) -> Update:
        if rec is None:
            raise RoomError("Комната не найдена. Проверьте код или ссылку")
        member = db.get(RoomMember, user_id)
        if member:
            if member.room_id == rec.id:
                rec.last_activity_at = self._activity_time()
                return Update(room_id=rec.id, room=self._room(rec), changed=False)
            raise RoomError("Вы уже в другой комнате. Сначала выйдите из неё")
        self._check_ban(db, user_id)
        locked = self._lock(db, rec.id)
        if locked is None:
            raise RoomError("Лобби больше неактивно. Попросите новое приглашение.")
        room = self._room(locked)
        room.join(user_id, name)
        self._store(locked, room, player_activity=True)
        db.add(RoomMember(user_id=user_id, room_id=rec.id))
        return Update(room_id=rec.id, room=room)

    def _check_join_limit(self, user_id):
        wait = self.join_limiter.wait_seconds(user_id)
        if wait:
            raise RoomError(f"Слишком много неудачных попыток входа. Повторите через {wait} с.")

    def join_code(self, user_id: int, name: str, code: str) -> Update:
        self._check_join_limit(user_id)
        code = (code or "").strip().upper()
        try:
            if re.fullmatch(r"[A-Z0-9]{6}", code) is None:
                raise RoomError("Код комнаты должен содержать 6 латинских букв или цифр")
            with self._factory.begin() as db:
                rec = db.execute(select(RoomRecord).where(RoomRecord.code == code)).scalar_one_or_none()
                result = self._join(db, rec, user_id, name)
        except RoomError:
            self.join_limiter.failed(user_id)
            raise
        self.join_limiter.succeeded(user_id)
        return result

    def join_token(self, user_id: int, name: str, token: str) -> Update:
        self._check_join_limit(user_id)
        try:
            if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,32}", token) is None:
                raise RoomError("Ссылка приглашения недействительна. Попросите новое приглашение.")
            with self._factory.begin() as db:
                rec = db.execute(select(RoomRecord).where(RoomRecord.invite_token == token)).scalar_one_or_none()
                if rec is None:
                    raise RoomError("Лобби неактивно или ссылка недействительна. Попросите новое приглашение.")
                result = self._join(db, rec, user_id, name)
        except RoomError:
            self.join_limiter.failed(user_id)
            raise
        self.join_limiter.succeeded(user_id)
        return result

    @staticmethod
    def _validate_panel(room, user_id, panel_id):
        if panel_id is None:
            return
        player = next((p for p in room.players if p["id"] == user_id), None)
        if player is None or player.get("panel") != panel_id:
            raise RoomError("Этот игровой экран устарел. Откройте «Моя комната».")

    def _leave_prompt(self, db, room, user_id):
        penalizable = room.phase in ("dealing", "playing") and room.round is not None and not room.is_test
        if not penalizable:
            return "Вы действительно хотите покинуть комнату?"
        strikes, minutes = self._penalty_preview(db, user_id, self._clock())
        return (
            "Выход во время раунда приведёт к блокировке онлайн-игр на "
            f"{format_duration(minutes * 60)} (нарушение №{strikes}).\n\n"
            "Вы действительно хотите выйти?"
        )

    def leave_confirmation(self, user_id: int, *, expected_room_id=None, expected_panel_id=None) -> str:
        """Return the confirmation-dialog text without changing room state."""
        with self._factory.begin() as db:
            member = db.get(RoomMember, user_id)
            if member is None:
                raise RoomError("Вы не находитесь в комнате")
            rec = db.get(RoomRecord, member.room_id)
            if rec is None:
                raise RoomError("Комната больше не существует")
            if expected_room_id is not None and member.room_id != expected_room_id:
                raise RoomError("Это подтверждение относится к другой комнате")
            room = self._room(rec)
            self._validate_panel(room, user_id, expected_panel_id)
            rec.last_activity_at = self._activity_time()
            return self._leave_prompt(db, room, user_id)

    def act(self, user_id: int, action: str, *, expected_room_id=None, expected_panel_id=None, expected_leave_text=None) -> Update:
        with self._factory.begin() as db:
            member = db.get(RoomMember, user_id)
            if member is None:
                raise RoomError("Вы не находитесь в комнате")
            if expected_room_id is not None and member.room_id != expected_room_id:
                raise RoomError("Этот экран относится к другой комнате. Откройте «Моя комната».")
            rec = self._lock(db, member.room_id)
            if rec is None:
                db.delete(member)
                raise RoomError("Комната больше не существует")
            room = self._room(rec)
            self._validate_panel(room, user_id, expected_panel_id)
            if expected_leave_text is not None and self._leave_prompt(db, room, user_id) != expected_leave_text:
                raise RoomError("Условия выхода изменились. Нажмите «Выйти» и подтвердите заново.")
            now = self._clock()
            if action == "leave_confirm":
                action = "leave"
            if action in ("start", "next") and self._packs is not None:
                available = self._packs.library_for(room.host_id)
                if any(not available.has(slug) for slug in room.settings.get("themes", [])):
                    raise RoomError("Выбранный набор удалён, заблокирован или стал недоступен. Выберите темы заново.")
            result = room.act(user_id, action, now)
            penalty = None
            if result.left and result.penalized:
                penalty = self._apply_penalty(db, user_id, now)
            if result.left:
                db.delete(member)
            if result.closed:
                db.flush()
                self._end_session(db, room.session_id, "abandoned")
                db.execute(delete(RoomMember).where(RoomMember.room_id == rec.id))
                db.delete(rec)
                return Update(
                    room_id=rec.id, room=None, left_user=user_id, changed=False, penalty=penalty
                )
            self._record_history(db, rec, room)
            if room.phase == "final":
                self._end_session(db, room.session_id, "finished")
            self._store(rec, room, player_activity=True)
            return Update(
                room_id=rec.id,
                room=room,
                alert=result.alert,
                changed=result.changed,
                left_user=user_id if result.left else None,
                penalty=penalty,
            )

    def say(self, user_id: int, text: str) -> Update:
        with self._factory.begin() as db:
            member = db.get(RoomMember, user_id)
            if member is None:
                raise RoomError("Вы не находитесь в комнате")
            rec = self._lock(db, member.room_id)
            if rec is None:
                raise RoomError("Комната больше не существует")
            room = self._room(rec)
            room.say(user_id, text, self._clock())
            self._store(rec, room, player_activity=True)
            return Update(room_id=rec.id, room=room)

    def due_room_ids(self) -> list[int]:
        with self._factory() as db:
            stmt = select(RoomRecord.id).where(
                RoomRecord.deadline.is_not(None), RoomRecord.deadline <= self._clock()
            )
            return [row[0] for row in db.execute(stmt)]

    def active_room_ids(self) -> list[int]:
        with self._factory() as db:
            stmt = select(RoomRecord.id).where(RoomRecord.deadline.is_not(None))
            return [row[0] for row in db.execute(stmt)]

    def tick_room(self, room_id: int) -> Update | None:
        with self._factory.begin() as db:
            rec = self._lock(db, room_id)
            if rec is None:
                return None
            room = self._room(rec)
            changed = room.tick(self._clock())
            self._record_history(db, rec, room)
            self._store(rec, room)
            return Update(room_id=rec.id, room=room, changed=changed)

    def set_panel(self, room_id: int, user_id: int, message_id: int, *, player_activity=False) -> None:
        with self._factory.begin() as db:
            rec = self._lock(db, room_id)
            if rec is None:
                return
            room = self._room(rec)
            for player in room.players:
                if player["id"] == user_id:
                    player["panel"] = message_id
            self._store(rec, room, player_activity=player_activity)

    def purge_stale(self, hours: int = 24) -> int:
        cutoff = self._activity_time() - timedelta(hours=hours)
        with self._factory.begin() as db:
            ids = [
                row[0]
                for row in db.execute(select(RoomRecord.id).where(RoomRecord.last_activity_at < cutoff).with_for_update())
            ]
            if ids:
                db.execute(
                    update(GameSession)
                    .where(GameSession.room_id.in_(ids), GameSession.status == "active")
                    .values(status="abandoned", ended_at=datetime.now(timezone.utc))
                )
            for room_id in ids:
                db.execute(delete(RoomMember).where(RoomMember.room_id == room_id))
                db.execute(delete(RoomRecord).where(RoomRecord.id == room_id))
            return len(ids)
