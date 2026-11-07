from dataclasses import dataclass
from enum import Enum


class Outcome(str, Enum):
    CIVILIANS_WIN = "civilians_win"
    SPY_WIN_VOTE = "spy_win_vote"
    SPY_WIN_GUESS = "spy_win_guess"
    SPY_WRONG_GUESS = "spy_wrong_guess"
    SPY_LEFT = "spy_left"
    DRAW = "draw"


@dataclass(frozen=True)
class ScoreRules:
    spy_win: int = 3
    civilians_win: int = 1
    draw: int = 0


def compute_scores(
    outcome: Outcome, spy_id: str, players: list[str], rules: ScoreRules
) -> dict[str, int]:
    deltas = {player: 0 for player in players}
    if outcome in (Outcome.CIVILIANS_WIN, Outcome.SPY_WRONG_GUESS, Outcome.SPY_LEFT):
        for player in players:
            if player != spy_id:
                deltas[player] = rules.civilians_win
    elif outcome in (Outcome.SPY_WIN_VOTE, Outcome.SPY_WIN_GUESS):
        deltas[spy_id] = rules.spy_win
    else:
        for player in players:
            deltas[player] = rules.draw
    return deltas
