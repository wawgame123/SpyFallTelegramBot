from dataclasses import dataclass

from sqlalchemy import Float, case, cast, func, select
from sqlalchemy.orm import sessionmaker

from app.database.models import GameSession, MatchRound, RoundPlayer, SessionPlayer

MIN_GAMES_FOR_RATE = 5
SORTS = ("points", "wins", "spy", "rate")


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
        if sort not in SORTS:
            sort = "points"
        points = func.sum(RoundPlayer.score_delta)
        games = func.count()
        wins = _wins()
        spy_wins = _spy_wins()
        stmt = _real_rounds(
            select(
                RoundPlayer.user_id,
                func.max(RoundPlayer.name),
                points,
                games,
                wins,
                spy_wins,
            )
        ).group_by(RoundPlayer.user_id)
        if sort == "points":
            stmt = stmt.order_by(points.desc(), wins.desc(), RoundPlayer.user_id)
        elif sort == "wins":
            stmt = stmt.order_by(wins.desc(), points.desc(), RoundPlayer.user_id)
        elif sort == "spy":
            stmt = stmt.order_by(spy_wins.desc(), points.desc(), RoundPlayer.user_id)
        else:
            rate = cast(wins, Float) / cast(games, Float)
            stmt = (
                stmt.having(games >= MIN_GAMES_FOR_RATE)
                .order_by(rate.desc(), games.desc(), RoundPlayer.user_id)
            )
        with self._factory() as db:
            return [
                RatingRow(
                    user_id=row[0],
                    name=row[1] or "Игрок",
                    points=int(row[2] or 0),
                    games=row[3],
                    wins=int(row[4] or 0),
                    spy_wins=int(row[5] or 0),
                )
                for row in db.execute(stmt.limit(limit))
            ]
