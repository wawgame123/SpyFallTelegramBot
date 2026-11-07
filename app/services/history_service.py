from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.database.models import GameSession, MatchRound, RoundPlayer, SessionPlayer


@dataclass
class UserRound:
    finished_at: object
    lobby_title: str
    theme: str
    word: str
    spy_name: str
    outcome: str
    role: str
    result: str
    score_delta: int
    round_id: int = 0
    pack_id: int | None = None
    pack_title: str | None = None


@dataclass
class LobbyRow:
    id: int
    title: str
    started_at: object
    ended_at: object
    status: str
    players: list[tuple[str, int]]
    winners: list[str]
    rounds_count: int
    player_ids: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class LobbyRound:
    round_no: int
    theme: str
    word: str
    spy_name: str
    outcome: str
    finished_at: object
    scores: list[tuple[str, str, int]]
    id: int = 0
    pack_id: int | None = None
    pack_title: str | None = None


def leaders(players: list[tuple[str, int]]) -> list[str]:
    if not players:
        return []
    top = max(score for _, score in players)
    if top <= 0:
        return []
    return [name for name, score in players if score == top]


class HistoryService:
    def __init__(self, factory: sessionmaker):
        self._factory = factory

    def user_rounds(self, user_id: int, page: int, size: int) -> tuple[list[UserRound], int]:
        with self._factory() as db:
            total = db.execute(
                select(func.count()).select_from(RoundPlayer).where(RoundPlayer.user_id == user_id)
            ).scalar_one()
            stmt = (
                select(RoundPlayer, MatchRound, GameSession)
                .join(MatchRound, RoundPlayer.round_id == MatchRound.id)
                .join(GameSession, MatchRound.session_id == GameSession.id)
                .where(RoundPlayer.user_id == user_id)
                .order_by(MatchRound.finished_at.desc(), MatchRound.id.desc())
                .limit(size)
                .offset(page * size)
            )
            rows = [
                UserRound(
                    finished_at=match.finished_at,
                    lobby_title=session.title,
                    theme=match.theme,
                    word=match.word,
                    spy_name=match.spy_name,
                    outcome=match.outcome,
                    role=rp.role,
                    result=rp.result,
                    score_delta=rp.score_delta,
                    round_id=match.id, pack_id=match.pack_id, pack_title=match.pack_title,
                )
                for rp, match, session in db.execute(stmt)
            ]
            return rows, total

    def _lobby_rows(self, db, sessions) -> list[LobbyRow]:
        if not sessions:
            return []
        ids = [s.id for s in sessions]
        grouped = {sid: [] for sid in ids}
        for player in db.execute(
            select(SessionPlayer).where(SessionPlayer.session_id.in_(ids))
            .order_by(SessionPlayer.score.desc(), SessionPlayer.name, SessionPlayer.user_id)
        ).scalars():
            grouped[player.session_id].append(player)
        counts = dict(db.execute(
            select(MatchRound.session_id, func.count())
            .where(MatchRound.session_id.in_(ids)).group_by(MatchRound.session_id)
        ).all())
        result = []
        for session in sessions:
            members = grouped[session.id]
            players = [(p.name, p.score) for p in members]
            result.append(LobbyRow(
                id=session.id, title=session.title, started_at=session.started_at,
                ended_at=session.ended_at, status=session.status, players=players,
                winners=leaders(players), rounds_count=counts.get(session.id, 0),
                player_ids=[(p.user_id, p.name) for p in members],
            ))
        return result

    def _lobby_row(self, db, session: GameSession) -> LobbyRow:
        return self._lobby_rows(db, [session])[0]

    def _is_member(self, db, user_id: int, session_id: int) -> bool:
        return db.get(SessionPlayer, (session_id, user_id)) is not None

    def user_lobbies(self, user_id: int, page: int, size: int) -> tuple[list[LobbyRow], int]:
        with self._factory() as db:
            total = db.execute(
                select(func.count()).select_from(SessionPlayer).where(SessionPlayer.user_id == user_id)
            ).scalar_one()
            stmt = (
                select(GameSession)
                .join(SessionPlayer, SessionPlayer.session_id == GameSession.id)
                .where(SessionPlayer.user_id == user_id)
                .order_by(GameSession.started_at.desc(), GameSession.id.desc())
                .limit(size)
                .offset(page * size)
            )
            sessions = db.execute(stmt).scalars().all()
            return self._lobby_rows(db, sessions), total

    def lobby(self, user_id: int, session_id: int) -> LobbyRow | None:
        with self._factory() as db:
            if not self._is_member(db, user_id, session_id):
                return None
            session = db.get(GameSession, session_id)
            return self._lobby_row(db, session) if session else None

    def lobby_rounds(
        self, user_id: int, session_id: int, page: int, size: int
    ) -> tuple[list[LobbyRound], int] | None:
        with self._factory() as db:
            if not self._is_member(db, user_id, session_id):
                return None
            total = db.execute(
                select(func.count()).select_from(MatchRound).where(MatchRound.session_id == session_id)
            ).scalar_one()
            stmt = (
                select(MatchRound)
                .where(MatchRound.session_id == session_id)
                .order_by(MatchRound.round_no)
                .limit(size)
                .offset(page * size)
            )
            matches = db.execute(stmt).scalars().all()
            grouped = {match.id: [] for match in matches}
            if matches:
                for player in db.execute(
                    select(RoundPlayer).where(RoundPlayer.round_id.in_(grouped))
                    .order_by(RoundPlayer.score_delta.desc(), RoundPlayer.name, RoundPlayer.user_id)
                ).scalars():
                    grouped[player.round_id].append((player.name, player.role, player.score_delta))
            result = []
            for match in matches:
                scores = grouped[match.id]
                result.append(
                    LobbyRound(
                        id=match.id, pack_id=match.pack_id, pack_title=match.pack_title,
                        round_no=match.round_no,
                        theme=match.theme,
                        word=match.word,
                        spy_name=match.spy_name,
                        outcome=match.outcome,
                        finished_at=match.finished_at,
                        scores=scores,
                    )
                )
            return result, total


    def round_detail(self, user_id: int, round_id: int) -> dict | None:
        with self._factory() as db:
            match = db.get(MatchRound, round_id)
            if match is None or not self._is_member(db, user_id, match.session_id):
                return None
            players = db.execute(select(RoundPlayer).where(RoundPlayer.round_id == round_id).order_by(RoundPlayer.score_delta.desc(), RoundPlayer.name)).scalars()
            return {"id": match.id, "session_id": match.session_id, "round_no": match.round_no,
                    "pack_id": match.pack_id, "pack_title": match.pack_title,
                    "theme": match.theme, "word": match.word,
                    "players": [(p.user_id, p.name, p.score_delta, p.role) for p in players]}

    def player_round(self, user_id: int, session_id: int, target: int) -> int | None:
        with self._factory() as db:
            if not self._is_member(db, user_id, session_id):
                return None
            return db.execute(select(MatchRound.id).join(RoundPlayer, RoundPlayer.round_id == MatchRound.id).where(MatchRound.session_id == session_id, RoundPlayer.user_id == target).order_by(MatchRound.id.desc()).limit(1)).scalar_one_or_none()
