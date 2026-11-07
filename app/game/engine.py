from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import Enum

from app.game.scoring import Outcome, ScoreRules, compute_scores
from app.game.voting import majority_reached, tally


class GameError(Exception):
    pass


class Phase(str, Enum):
    ROLE_DEAL = "role_deal"
    DISCUSSION = "discussion"
    SUSPECT_VOTE = "suspect_vote"
    RUNOFF_VOTE = "runoff_vote"
    LAST_CHANCE = "last_chance"
    FINISHED = "finished"


def normalize(word: str) -> str:
    return " ".join(word.casefold().split())


@dataclass
class Round:
    players: list[str]
    spy_id: str
    word: str
    theme: str
    allowed_words: list[str]
    order: list[str]
    guesses_left: int = 1
    phase: Phase = Phase.ROLE_DEAL
    turn_idx: int = 0
    theme_id: str = ""
    open_guess: bool = False
    circle_no: int = 1
    circle_votes: dict[str, bool] = field(default_factory=dict)
    votes: dict[str, str] = field(default_factory=dict)
    candidates: list[str] = field(default_factory=list)
    secret_guess_word: str | None = None
    secret_guess_correct: bool | None = None
    outcome: Outcome | None = None
    settled: bool = False

    def _require_player(self, player_id: str) -> None:
        if player_id not in self.players:
            raise GameError("Игрок не участвует в раунде")

    def _require_phase(self, *phases: Phase) -> None:
        if self.phase not in phases:
            raise GameError("Действие недоступно в текущей фазе")

    def finish_deal(self) -> None:
        self._require_phase(Phase.ROLE_DEAL)
        self.phase = Phase.DISCUSSION

    def current(self) -> str:
        return self.order[self.turn_idx % len(self.order)]

    def next_player(self) -> str:
        return self.order[(self.turn_idx + 1) % len(self.order)]

    def end_turn(self, player_id: str, wants_vote: bool = False) -> str:
        self._require_phase(Phase.DISCUSSION)
        if player_id != self.current():
            raise GameError("Сейчас не ваш ход")
        self.circle_votes[player_id] = bool(wants_vote)
        self.turn_idx += 1
        if self.turn_idx < len(self.order):
            return "next"
        return self._close_circle()

    def _close_circle(self) -> str:
        self.turn_idx = 0
        yes = sum(1 for value in self.circle_votes.values() if value)
        self.circle_votes = {}
        if majority_reached(yes, len(self.players)):
            if self.secret_guess_correct is True:
                self._finish(Outcome.SPY_WIN_GUESS)
                return "spy_won"
            if self.secret_guess_correct is False:
                self._finish(Outcome.SPY_WRONG_GUESS)
                return "spy_lost"
            self.phase = Phase.SUSPECT_VOTE
            self.votes = {}
            return "vote"
        self.circle_no += 1
        return "new_circle"

    def spy_left(self) -> None:
        self._finish(Outcome.SPY_LEFT)

    def remove_player(self, player_id: str) -> str:
        if player_id == self.spy_id:
            raise GameError("Шпион уходит через spy_left")
        self._require_player(player_id)
        if self.phase == Phase.FINISHED:
            raise GameError("Раунд уже завершён")
        index = self.order.index(player_id)
        self.players.remove(player_id)
        self.order.remove(player_id)
        self.circle_votes.pop(player_id, None)
        self.votes = {
            voter: target
            for voter, target in self.votes.items()
            if voter != player_id and target != player_id
        }
        if self.phase == Phase.DISCUSSION:
            if index < self.turn_idx:
                self.turn_idx -= 1
            if self.turn_idx >= len(self.order):
                return self._close_circle()
            return "next"
        if self.phase == Phase.RUNOFF_VOTE and player_id in self.candidates:
            self.candidates.remove(player_id)
            if len(self.candidates) <= 1:
                survivor = self.candidates[0] if self.candidates else None
                if self.secret_guess_correct is True:
                    self._finish(Outcome.DRAW)
                elif survivor == self.spy_id:
                    self._finish(Outcome.CIVILIANS_WIN)
                else:
                    self._finish(Outcome.SPY_WIN_VOTE)
                return "finished"
        if self.phase in (Phase.SUSPECT_VOTE, Phase.RUNOFF_VOTE):
            if self.players and len(self.votes) == len(self.players):
                self._resolve_votes()
        return "next"

    def secret_guess(self, player_id: str, word: str) -> None:
        if self.open_guess or self.phase not in (
            Phase.DISCUSSION,
            Phase.SUSPECT_VOTE,
            Phase.RUNOFF_VOTE,
        ):
            return
        if player_id != self.spy_id or self.guesses_left <= 0:
            return
        if normalize(word) not in {normalize(w) for w in self.allowed_words}:
            return
        self.guesses_left -= 1
        self.secret_guess_word = word
        self.secret_guess_correct = normalize(word) == normalize(self.word)

    def valid_targets(self, voter_id: str) -> list[str]:
        pool = self.candidates if self.phase == Phase.RUNOFF_VOTE else self.players
        return [p for p in pool if p != voter_id]

    def cast_vote(self, voter_id: str, target_id: str) -> None:
        self._require_phase(Phase.SUSPECT_VOTE, Phase.RUNOFF_VOTE)
        self._require_player(voter_id)
        if target_id not in self.valid_targets(voter_id):
            raise GameError("Недопустимая цель голосования")
        self.votes[voter_id] = target_id
        if len(self.votes) == len(self.players):
            self._resolve_votes()

    def _resolve_votes(self) -> None:
        _, leaders = tally(self.votes)
        guessed = self.secret_guess_correct is True
        if len(leaders) == 1:
            if guessed:
                self._finish(Outcome.DRAW)
            elif leaders[0] == self.spy_id:
                self._finish(Outcome.CIVILIANS_WIN)
            else:
                self._finish(Outcome.SPY_WIN_VOTE)
        elif self.phase == Phase.SUSPECT_VOTE:
            self.candidates = leaders
            self.votes = {}
            self.phase = Phase.RUNOFF_VOTE
        else:
            self._finish(Outcome.DRAW if guessed else Outcome.SPY_WIN_VOTE)

    def guess(self, player_id: str, word: str) -> bool:
        allowed = [Phase.SUSPECT_VOTE, Phase.RUNOFF_VOTE, Phase.LAST_CHANCE]
        if self.open_guess:
            allowed.append(Phase.DISCUSSION)
        self._require_phase(*allowed)
        if player_id != self.spy_id:
            raise GameError("Угадывать слово может только шпион")
        if self.guesses_left <= 0:
            raise GameError("Попытки закончились")
        if normalize(word) not in {normalize(w) for w in self.allowed_words}:
            raise GameError("Слово не входит в выбранные темы")
        self.guesses_left -= 1
        correct = normalize(word) == normalize(self.word)
        if self.phase == Phase.LAST_CHANCE:
            self._finish(Outcome.DRAW if correct else Outcome.CIVILIANS_WIN)
        elif self.phase == Phase.DISCUSSION:
            self._finish(Outcome.SPY_WIN_GUESS if correct else Outcome.SPY_WRONG_GUESS)
        elif correct:
            self._finish(Outcome.DRAW)
        return correct

    def skip_last_chance(self) -> None:
        self._require_phase(Phase.LAST_CHANCE)
        self._finish(Outcome.CIVILIANS_WIN)

    def _finish(self, outcome: Outcome) -> None:
        if self.phase == Phase.FINISHED:
            raise GameError("Раунд уже завершён")
        self.outcome = outcome
        self.phase = Phase.FINISHED

    def settle(self, rules: ScoreRules) -> dict[str, int]:
        self._require_phase(Phase.FINISHED)
        if self.settled or self.outcome is None:
            raise GameError("Очки за раунд уже начислены")
        self.settled = True
        return compute_scores(self.outcome, self.spy_id, self.players, rules)

    def to_dict(self) -> dict:
        return {
            "players": list(self.players),
            "spy_id": self.spy_id,
            "word": self.word,
            "theme": self.theme,
            "allowed_words": list(self.allowed_words),
            "order": list(self.order),
            "guesses_left": self.guesses_left,
            "phase": self.phase.value,
            "turn_idx": self.turn_idx,
            "theme_id": self.theme_id,
            "open_guess": self.open_guess,
            "circle_no": self.circle_no,
            "circle_votes": dict(self.circle_votes),
            "votes": dict(self.votes),
            "candidates": list(self.candidates),
            "secret_guess_word": self.secret_guess_word,
            "secret_guess_correct": self.secret_guess_correct,
            "outcome": self.outcome.value if self.outcome else None,
            "settled": self.settled,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Round:
        known = {f.name for f in fields(cls)}
        payload = {k: v for k, v in data.items() if k in known}
        payload["phase"] = Phase(payload["phase"])
        payload["outcome"] = Outcome(payload["outcome"]) if payload.get("outcome") else None
        return cls(**payload)
