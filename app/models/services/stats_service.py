from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Float, case, cast, func, select
from sqlalchemy.orm import sessionmaker

from app.models.database.models import GameSession, MatchRound, RoundPlayer, SessionPlayer

MIN_GAMES_FOR_RATE = 5
SORTS = ("points", "wins", "spy", "rate")
PERIOD_DAYS = {"all": None, "week": 7, "month": 30}


@dataclass
class Profile:
    user_id: int
    name: str
    games: int
    wins: int
    civilian_wins: int
    spy_wins: int
    losses: int
    draws: int
    points: int
    lobbies: int

    @property
    def win_rate(self) -> float:
        return round(100 * self.wins / self.games, 1) if self.games else 0.0


@dataclass
class RatingRow:
    user_id: int
    name: str
    points: int
    games: int
    wins: int
    spy_wins: int
    place: int = 0

    @property
    def win_rate(self) -> float:
        return round(100 * self.wins / self.games, 1) if self.games else 0.0


def _wins():
    return func.sum(case((RoundPlayer.result == "win", 1), else_=0))


def _spy_wins():
    return func.sum(
        case(((RoundPlayer.role == "spy") & (RoundPlayer.result == "win"), 1), else_=0)
    )


def _real_rounds(stmt):
    return stmt.join(MatchRound, RoundPlayer.round_id == MatchRound.id).join(
        GameSession, MatchRound.session_id == GameSession.id
    ).where(GameSession.is_test.is_(False))


class StatsService:
    def __init__(self, factory: sessionmaker):
        self._factory = factory

    def profile(self, user_id: int) -> Profile | None:
        with self._factory() as db:
            row = db.execute(
                _real_rounds(
                    select(
                        func.count(),
                        _wins(),
                        _spy_wins(),
                        func.sum(case((RoundPlayer.result == "loss", 1), else_=0)),
                        func.sum(case((RoundPlayer.result == "draw", 1), else_=0)),
                        func.sum(RoundPlayer.score_delta),
                        func.max(RoundPlayer.name),
                    )
                ).where(RoundPlayer.user_id == user_id)
            ).one()
            games = row[0] or 0
            if games == 0:
                return None
            lobbies = db.execute(
                select(func.count())
                .select_from(SessionPlayer)
                .join(GameSession, SessionPlayer.session_id == GameSession.id)
                .where(SessionPlayer.user_id == user_id, GameSession.is_test.is_(False))
            ).scalar_one()
            wins = int(row[1] or 0)
            spy_wins = int(row[2] or 0)
            return Profile(
                user_id=user_id,
                name=row[6] or "Игрок",
                games=games,
                wins=wins,
                civilian_wins=wins - spy_wins,
                spy_wins=spy_wins,
                losses=int(row[3] or 0),
                draws=int(row[4] or 0),
                points=int(row[5] or 0),
                lobbies=lobbies,
            )

    def leaderboard(self, sort: str = "points", limit: int = 10) -> list[RatingRow]:
        return self.rating(sort, limit=limit)[0]

    def rating(
        self, sort: str = "points", period: str = "all", viewer_id: int | None = None,
        *, limit: int = 10,
    ) -> tuple[list[RatingRow], RatingRow | None]:
        if sort not in SORTS:
            sort = "points"
        points = func.sum(RoundPlayer.score_delta)
        games = func.count()
        wins = _wins()
        spy_wins = _spy_wins()
        user = RoundPlayer.user_id
        if sort == "points":
            order = (points.desc(), wins.desc(), user)
        elif sort == "wins":
            order = (wins.desc(), points.desc(), user)
        elif sort == "spy":
            order = (spy_wins.desc(), points.desc(), user)
        else:
            rate = cast(wins, Float) / cast(games, Float)
            order = (rate.desc(), games.desc(), user)
        stmt = _real_rounds(
            select(
                user,
                func.max(RoundPlayer.name),
                points,
                games,
                wins,
                spy_wins,
                func.row_number().over(order_by=order).label("place"),
            )
        ).group_by(user)
        days = PERIOD_DAYS.get(period)
        if days:
            now = datetime.now(timezone.utc)
            # MySQL DATETIME may round a just-written timestamp up to the next second.
            stmt = stmt.where(MatchRound.finished_at.between(
                now - timedelta(days=days), now + timedelta(seconds=1),
            ))
        if sort == "rate":
            stmt = stmt.having(games >= MIN_GAMES_FOR_RATE)
        ranked = stmt.subquery()
        query = select(ranked).where(ranked.c.place <= limit)
        if viewer_id is not None:
            query = select(ranked).where((ranked.c.place <= limit) | (ranked.c.user_id == viewer_id))
        with self._factory() as db:
            rows = [
                RatingRow(
                    user_id=row[0],
                    name=row[1] or "Игрок",
                    points=int(row[2] or 0),
                    games=row[3],
                    wins=int(row[4] or 0),
                    spy_wins=int(row[5] or 0),
                    place=row[6],
                )
                for row in db.execute(query.order_by(ranked.c.place))
            ]
        return [r for r in rows if r.place <= limit], next((r for r in rows if r.place > limit), None)
