from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

from app.game.engine import GameError, Phase, Round
from app.game.packs import PackLibrary, load_default_library
from app.game.roundfactory import create_round
from app.game.scoring import Outcome, ScoreRules

MIN_PLAYERS = 3
MAX_PLAYERS = 12
MAX_NAME_LEN = 20
PAGE_SIZE = 8
THEMES_PAGE = 8

REASONS = {
    Outcome.CIVILIANS_WIN: "Игроки нашли шпиона, и он не угадал слово.",
    Outcome.SPY_WRONG_GUESS: "Шпион ошибся при угадывании слова и проиграл.",
    Outcome.SPY_LEFT: "Шпион покинул игру. Победа игроков.",
    Outcome.SPY_WIN_VOTE: "Игроки ошиблись или не смогли определить шпиона.",
    Outcome.SPY_WIN_GUESS: "Шпион угадал секретное слово.",
    Outcome.DRAW: "Шпион угадал слово во время голосования. Ничья.",
}


class Stage(str, Enum):
    COUNT = "count"
    NAMES = "names"
    REVIEW = "review"
    THEMES = "themes"
    DEAL_HANDOFF = "deal_handoff"
    DEAL_REVEAL = "deal_reveal"
    DEAL_DONE = "deal_done"
    DISCUSSION = "discussion"
    VOTE_REQUEST = "vote_request"
    VOTE_HANDOFF = "vote_handoff"
    VOTE_PICK = "vote_pick"
    GUESS_WHO = "guess_who"
    GUESS_HANDOFF = "guess_handoff"
    GUESS_PICK = "guess_pick"
    GUESS_CONFIRM = "guess_confirm"
    RESULT = "result"
    FINAL = "final"


class Reject(Exception):
    pass


@dataclass
class Screen:
    text: str
    rows: list[list[tuple[str, str]]]
    html: bool = False


@dataclass
class Response:
    screen: Screen | None = None
    alert: str | None = None
    exit: bool = False


def validate_name(raw: str, existing: list[str], skip: int | None = None) -> str:
    name = " ".join(raw.split())
    if not name:
        raise ValueError("Имя не может быть пустым")
    if len(name) > MAX_NAME_LEN:
        raise ValueError(f"Имя длиннее {MAX_NAME_LEN} символов")
    taken = [n.casefold() for i, n in enumerate(existing) if i != skip]
    if name.casefold() in taken:
        raise ValueError("Такое имя уже есть")
    return name


@dataclass
class LocalSession:
    library: PackLibrary = field(default_factory=load_default_library, repr=False)
    rng: random.Random = field(default_factory=random.Random, repr=False)
    rules: ScoreRules = field(default_factory=ScoreRules, repr=False)
    stage: Stage = Stage.COUNT
    seq: int = 0
    count: int = 0
    names: list[str] = field(default_factory=list)
    edit_idx: int | None = None
    themes: list[str] = field(default_factory=list)
    random_theme: bool = False
    scores: dict[str, int] = field(default_factory=dict)
    round_no: int = 0
    used_words: list[str] = field(default_factory=list)
    previous_spy: str | None = None
    round: Round | None = None
    deal_idx: int = 0
    revealed: bool = False
    vote_idx: int = 0
    turn_idx: int = 0
    theme_page: int = 0
    guess_actor: str | None = None
    guess_return: Stage = Stage.DISCUSSION
    pending_alert: str | None = field(default=None, repr=False)
    guess_page: int = 0
    guess_word: str | None = None
    history: list[dict] = field(default_factory=list)
    last_deltas: dict[str, int] = field(default_factory=dict)
    message_id: int | None = None
    notice: str = ""

    @property
    def pids(self) -> list[str]:
        return [f"p{i}" for i in range(len(self.names))]

    def name_of(self, pid: str) -> str:
        return self.names[int(pid[1:])]

    def table(self) -> str:
        ranked = sorted(self.pids, key=lambda p: (-self.scores.get(p, 0), int(p[1:])))
        return "\n".join(
            f"{i}. {self.name_of(p)} — {self.scores.get(p, 0)}"
            for i, p in enumerate(ranked, 1)
        )

    def _themes_label(self) -> str:
        if not self.themes:
            return "не выбраны"
        return ", ".join(self.library.get(t).title for t in self.themes)

    def _guess_words(self) -> list[str]:
        assert self.round is not None
        return sorted(self.round.allowed_words, key=str.casefold)

    def _spy(self) -> str:
        assert self.round is not None
        return self.round.spy_id

    def screen(self) -> Screen:
        notice = f"{self.notice}\n\n" if self.notice else ""
        screen = self._build_screen()
        return Screen(notice + screen.text, screen.rows)

    def _build_screen(self) -> Screen:
        stage = self.stage
        if stage == Stage.COUNT:
            numbers = [(str(n), f"n:{n}") for n in range(MIN_PLAYERS, MAX_PLAYERS + 1)]
            rows = [numbers[i : i + 5] for i in range(0, len(numbers), 5)]
            if self.names:
                rows.append([("Назад", "rv")])
            rows.append([("В меню", "menu")])
            return Screen(
                f"Игра на одном устройстве\n\nСколько игроков? ({MIN_PLAYERS}–{MAX_PLAYERS})",
                rows,
            )
        if stage == Stage.NAMES:
            return Screen(
                f"Игрок {len(self.names) + 1} из {self.count}\n\nНапишите имя сообщением в чат.",
                [[("Количество игроков", "cnt")], [("В меню", "menu")]],
            )
        if stage == Stage.REVIEW:
            if self.edit_idx is not None:
                return Screen(
                    f"Новое имя для игрока {self.edit_idx + 1} "
                    f"(сейчас: {self.names[self.edit_idx]}).\n\nНапишите имя сообщением.",
                    [[("Отмена", "ce")]],
                )
            lines = "\n".join(f"{i}. {n}" for i, n in enumerate(self.names, 1))
            head = "Настройка игры"
            if self.round_no:
                head += f" (сыграно раундов: {self.round_no}, очки сохранены)"
            edit = [(f"{i}", f"e:{i - 1}") for i in range(1, len(self.names) + 1)]
            rows = [edit[i : i + 6] for i in range(0, len(edit), 6)]
            rows += [
                [("Темы", "th"), ("Количество", "cnt")],
                [("Начать игру", "go")],
                [("В меню", "menu")],
            ]
            return Screen(
                f"{head}\n\nИгроки:\n{lines}\n\nТемы: {self._themes_label()}"
                f"\nПорядок тем: {'случайный' if self.random_theme else 'по очереди'}"
                "\n\n— исправить имя игрока по номеру.",
                rows,
            )
        if stage == Stage.THEMES:
            rows = []
            all_packs = self.library.packs()
            pages = max(1, -(-len(all_packs) // THEMES_PAGE))
            page = min(self.theme_page, pages - 1)
            for pack in all_packs[page * THEMES_PAGE : (page + 1) * THEMES_PAGE]:
                mark = "[x]" if pack.id in self.themes else "[ ]"
                rows.append([(f"{mark} {pack.title} ({len(pack.words)})", f"t:{pack.id}")])
            nav = []
            if page > 0:
                nav.append(("Назад", f"tp:{page - 1}"))
            if page < pages - 1:
                nav.append(("Вперёд", f"tp:{page + 1}"))
            if nav:
                rows.append(nav)
            order = "случайный" if self.random_theme else "по очереди"
            rows.append([("Выбрать все", "tall"), ("Очистить", "tnone")])
            rows.append([(f"Порядок тем: {order}", "rt")])
            rows.append([("Готово", "ok")])
            return Screen(
                "Выберите одну или несколько тем.\n\n"
                "В каждом раунде играет одна тема из отмеченных: по очереди или в случайном порядке.",
                rows,
            )
        if stage == Stage.DEAL_HANDOFF:
            name = self.names[self.deal_idx]
            return Screen(
                f"Раздача ролей · {self.deal_idx + 1} из {len(self.names)}\n\n"
                f"Передайте телефон игроку {name}.\n\nОстальные, не смотрите на экран.",
                [[("Показать роль", "me")]],
            )
        if stage == Stage.DEAL_REVEAL:
            name = self.names[self.deal_idx]
            return Screen(
                f"{name}, это ваш экран.\n\n"
                "Нажмите «Показать»: роль появится во всплывающем окне и не останется в чате."
                "\n\nЗапомните её и нажмите «Дальше».",
                [[("Показать", "show")], [("Дальше", "next")]],
            )
        if stage == Stage.DEAL_DONE and self.round is not None:
            return Screen(
                "Все игроки получили свои роли.\n\n"
                f"Тема раунда: {self.round.theme}\n\n"
                "Игра начинается!",
                [[("Начать раунд", "start")]],
            )
        if stage == Stage.DISCUSSION:
            order = self.round.order
            idx = self.round.turn_idx % len(order)
            listing = "\n".join(
                f"{'>' if i == idx else '-'} {i + 1}. {self.name_of(p)}"
                for i, p in enumerate(order)
            )
            return Screen(
                f"Раунд {self.round_no}, круг {self.round.circle_no}. Тема: {self.round.theme}\n\n"
                f"Говорит: {self.name_of(self.round.current())}\n"
                f"Далее: {self.name_of(self.round.next_player())}\n\n"
                f"Порядок:\n{listing}\n\n"
                "В свой ход каждый решает, нужно ли голосование. Оно начнётся после круга, "
                "если за него больше половины игроков.",
                [
                    [("Передать ход", "nx:0")],
                    [("Передать ход и предложить голосование", "nx:1")],
                    [("Угадать слово", "gu")],
                ],
            )
        if stage == Stage.VOTE_HANDOFF:
            name = self.name_of(self.round.players[self.vote_idx])
            extra = ""
            if self.round.phase == Phase.RUNOFF_VOTE:
                names = ", ".join(self.name_of(p) for p in self.round.candidates)
                extra = f"\n\nДополнительное голосование между: {names}."
            return Screen(
                f"Тайное голосование · {self.vote_idx + 1} из {len(self.round.players)}.{extra}"
                f"\n\nПередайте телефон игроку {name}.",
                [[("Голосовать", "me")], [("Угадать слово", "gu")]],
            )
        if stage == Stage.VOTE_PICK:
            voter = self.round.players[self.vote_idx]
            rows = [
                [(self.name_of(t), f"v:{t}")] for t in self.round.valid_targets(voter)
            ]
            return Screen(f"{self.name_of(voter)}, кого вы считаете шпионом?", rows)
        if stage == Stage.GUESS_WHO:
            rows = [[(self.name_of(p), f"gw:{p}")] for p in self.round.players]
            rows.append([("Назад", "back")])
            return Screen(
                "Попытка угадать слово.\n\n"
                "Возьмите телефон и выберите своё имя. Остальные, отвернитесь.",
                rows,
            )
        if stage == Stage.GUESS_HANDOFF:
            name = self.name_of(self._spy())
            return Screen(
                f"Шпион определён. Игрок {name}, у вас последний шанс угадать слово."
                f"\n\nПередайте телефон игроку {name}.",
                [[("Угадать слово", "me")]],
            )
        if stage == Stage.GUESS_PICK:
            words = self._guess_words()
            pages = max(1, -(-len(words) // PAGE_SIZE))
            page = min(self.guess_page, pages - 1)
            chunk = words[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
            rows = []
            for i in range(0, len(chunk), 2):
                rows.append(
                    [
                        (chunk[j], f"gp:{page * PAGE_SIZE + j}")
                        for j in range(i, min(i + 2, len(chunk)))
                    ]
                )
            nav = []
            if page > 0:
                nav.append(("Назад", f"pg:{page - 1}"))
            if page < pages - 1:
                nav.append(("Вперёд", f"pg:{page + 1}"))
            if nav:
                rows.append(nav)
            if self.round.phase == Phase.LAST_CHANCE:
                rows.append([("Не угадывать", "skip")])
            else:
                rows.append([("Отмена", "cancel")])
            return Screen(
                f"Выберите слово. Тема: {self.round.theme}. Страница {page + 1}/{pages}",
                rows,
            )
        if stage == Stage.GUESS_CONFIRM:
            return Screen(
                f"Вы выбрали: {self.guess_word}\n\nОтправить ответ? Попытка одна. "
                "Результат станет известен в конце раунда.",
                [[("Да", "yes"), ("Другое слово", "no")]],
            )
        if stage == Stage.RESULT:
            return self._result_screen()
        return Screen(
            f"Игра завершена!\n\nИтоговая таблица:\n{self.table()}",
            [[("В меню", "menu")]],
        )

    def _secret_guess_line(self) -> str:
        rnd = self.round
        if rnd is None or not rnd.secret_guess_word:
            return ""
        verdict = "верно" if rnd.secret_guess_correct else "неверно"
        return f"\n\nТайная попытка шпиона: {rnd.secret_guess_word} ({verdict})"

    def _result_screen(self) -> Screen:
        info = self.history[-1]
        lines = []
        for pid in self.pids:
            delta = info["deltas"].get(pid, 0)
            lines.append(f"{self.name_of(pid)}: +{delta}")
        return Screen(
            f"Итоги раунда {info['round']}\n\n"
            f"Шпион: {self.name_of(info['spy'])}\n"
            f"Слово: {info['word']}\n"
            f"{REASONS[Outcome(info['outcome'])]}\n\n"
            f"Очки за раунд:\n" + "\n".join(lines) + f"\n\nТаблица:\n{self.table()}"
            + self._secret_guess_line(),
            [
                [("Следующий раунд", "nr")],
                [("Изменить настройки", "set")],
                [("Завершить игру", "end")],
                [("В меню", "menu")],
            ],
        )

    def act(self, seq: int, action: str) -> Response:
        if seq != self.seq:
            return Response(screen=self.screen(), alert="Эта кнопка устарела")
        if action == "show":
            try:
                return Response(alert=self._role_text())
            except Reject as err:
                return Response(screen=self.screen(), alert=str(err))
        if action == "menu" and self.stage in (
            Stage.COUNT, Stage.NAMES, Stage.REVIEW, Stage.RESULT, Stage.FINAL
        ):
            return Response(exit=True)
        self.notice = ""
        try:
            self._dispatch(action)
        except (Reject, GameError) as err:
            return Response(screen=self.screen(), alert=str(err))
        alert = self.pending_alert
        self.pending_alert = None
        self.seq += 1
        return Response(screen=self.screen(), alert=alert)

    def _role_text(self) -> str:
        if self.stage != Stage.DEAL_REVEAL or self.round is None:
            raise Reject("Роль сейчас недоступна")
        self.revealed = True
        pid = self.round.players[self.deal_idx]
        name = self.name_of(pid)
        if pid == self.round.spy_id:
            return f"{name}, вы шпион! Определите загаданное слово."
        return f"{name}, ваше загаданное слово:\n\n{self.round.word.upper()}"

    def _dispatch(self, action: str) -> None:
        stage = self.stage
        name, _, arg = action.partition(":")
        if stage == Stage.COUNT:
            if name == "n" and arg.isdigit() and MIN_PLAYERS <= int(arg) <= MAX_PLAYERS:
                self.count = int(arg)
                self.names = self.names[: self.count]
                self.stage = Stage.REVIEW if len(self.names) == self.count else Stage.NAMES
                return
            if name == "rv" and self.names:
                self.stage = Stage.REVIEW
                return
        elif stage == Stage.NAMES:
            if name == "cnt":
                self.stage = Stage.COUNT
                return
        elif stage == Stage.REVIEW:
            if name == "e" and arg.isdigit() and int(arg) < len(self.names):
                self.edit_idx = int(arg)
                return
            if name == "ce":
                self.edit_idx = None
                return
            if self.edit_idx is not None:
                raise Reject("Сначала введите имя или нажмите «Отмена»")
            if name == "th":
                self.stage = Stage.THEMES
                return
            if name == "cnt":
                self.stage = Stage.COUNT
                return
            if name == "go":
                self._start_game()
                return
        elif stage == Stage.THEMES:
            if name == "t" and self.library.has(arg):
                if arg in self.themes:
                    self.themes.remove(arg)
                else:
                    self.themes.append(arg)
                return
            if name == "tp" and arg.isdigit():
                self.theme_page = int(arg)
                return
            if name == "tall":
                self.themes = [pack.id for pack in self.library.packs()]
                return
            if name == "tnone":
                self.themes = []
                return
            if name == "rt":
                self.random_theme = not self.random_theme
                return
            if name == "ok":
                self.stage = Stage.REVIEW
                return
        elif stage == Stage.DEAL_HANDOFF:
            if name == "me":
                self.revealed = False
                self.stage = Stage.DEAL_REVEAL
                return
        elif stage == Stage.DEAL_REVEAL:
            if name == "next":
                if not self.revealed:
                    raise Reject("Сначала посмотрите свою роль")
                self.revealed = False
                self.deal_idx += 1
                if self.deal_idx >= len(self.names):
                    self.stage = Stage.DEAL_DONE
                else:
                    self.stage = Stage.DEAL_HANDOFF
                return
        elif stage == Stage.DEAL_DONE:
            if name == "start":
                self.round.finish_deal()
                self.turn_idx = 0
                self.stage = Stage.DISCUSSION
                return
        elif stage == Stage.DISCUSSION:
            if name == "nx":
                event = self.round.end_turn(self.round.current(), arg == "1")
                self._after_turn(event)
                return
            if name == "gu":
                self.guess_return = Stage.DISCUSSION
                self.stage = Stage.GUESS_WHO
                return
        elif stage == Stage.VOTE_HANDOFF:
            if name == "me":
                self.stage = Stage.VOTE_PICK
                return
            if name == "gu":
                self.guess_return = Stage.VOTE_HANDOFF
                self.stage = Stage.GUESS_WHO
                return
        elif stage == Stage.VOTE_PICK:
            if name == "v":
                voter = self.round.players[self.vote_idx]
                phase_before = self.round.phase
                self.round.cast_vote(voter, arg)
                self.vote_idx += 1
                self._after_vote(phase_before)
                return
        elif stage == Stage.GUESS_WHO:
            if name == "back":
                self.stage = self.guess_return
                return
            if name == "gw" and arg in self.round.players:
                self.guess_actor = arg
                self.guess_page = 0
                self.stage = Stage.GUESS_PICK
                return
        elif stage == Stage.GUESS_HANDOFF:
            if name == "me":
                self.guess_actor = self._spy()
                self.guess_page = 0
                self.stage = Stage.GUESS_PICK
                return
        elif stage == Stage.GUESS_PICK:
            if name == "pg" and arg.isdigit():
                self.guess_page = int(arg)
                return
            if name == "gp" and arg.isdigit() and int(arg) < len(self._guess_words()):
                self.guess_word = self._guess_words()[int(arg)]
                self.stage = Stage.GUESS_CONFIRM
                return
            if name == "cancel" and self.round.phase != Phase.LAST_CHANCE:
                self.stage = self.guess_return
                return
            if name == "skip" and self.round.phase == Phase.LAST_CHANCE:
                self.round.skip_last_chance()
                self._finish_round()
                return
        elif stage == Stage.GUESS_CONFIRM:
            if name == "no":
                self.stage = Stage.GUESS_PICK
                return
            if name == "yes":
                word = self.guess_word or ""
                actor = self.guess_actor or ""
                self.guess_word = None
                if self.round.phase == Phase.LAST_CHANCE:
                    self.round.guess(self._spy(), word)
                    self._finish_round()
                    return
                self.round.secret_guess(actor, word)
                self.pending_alert = "Ответ принят."
                self.stage = self.guess_return
                return
        elif stage == Stage.RESULT:
            if name == "nr":
                self._start_round()
                return
            if name == "set":
                self.stage = Stage.REVIEW
                return
            if name == "end":
                self.stage = Stage.FINAL
                return
        raise Reject("Это действие сейчас недоступно")

    def _after_turn(self, event: str) -> None:
        if self.round.phase == Phase.FINISHED:
            self._finish_round()
        elif event == "vote":
            self.vote_idx = 0
            self.stage = Stage.VOTE_HANDOFF
            self.notice = "Круг завершён: большинство за голосование."
        elif event == "new_circle":
            self.notice = (
                "Круг завершён: большинство против голосования. "
                f"Начинается круг {self.round.circle_no}."
            )

    def _after_vote(self, phase_before: Phase) -> None:
        phase = self.round.phase
        if phase == Phase.FINISHED:
            self._finish_round()
        elif phase == Phase.LAST_CHANCE:
            self.stage = Stage.GUESS_HANDOFF
        elif phase == Phase.RUNOFF_VOTE and phase_before != Phase.RUNOFF_VOTE:
            self.vote_idx = 0
            self.stage = Stage.VOTE_HANDOFF
        else:
            self.stage = Stage.VOTE_HANDOFF

    def handle_text(self, text: str) -> Response | None:
        if self.stage == Stage.NAMES:
            try:
                self.names.append(validate_name(text, self.names))
            except ValueError as err:
                self.notice = str(err)
                return Response(screen=self.screen())
            self.notice = ""
            if len(self.names) >= self.count:
                self.stage = Stage.REVIEW
            self.seq += 1
            return Response(screen=self.screen())
        if self.stage == Stage.REVIEW and self.edit_idx is not None:
            try:
                self.names[self.edit_idx] = validate_name(text, self.names, self.edit_idx)
            except ValueError as err:
                self.notice = str(err)
                return Response(screen=self.screen())
            self.notice = ""
            self.edit_idx = None
            self.seq += 1
            return Response(screen=self.screen())
        return None

    def _start_game(self) -> None:
        if not MIN_PLAYERS <= len(self.names) <= MAX_PLAYERS:
            raise Reject("Некорректное число игроков")
        if not self.themes:
            raise Reject("Выберите хотя бы одну тему")
        self.scores = {p: self.scores.get(p, 0) for p in self.pids}
        self._start_round()

    def _start_round(self) -> None:
        pids = self.pids
        try:
            self.round, self.used_words = create_round(
                self.library,
                self.rng,
                pids,
                self.themes,
                self.random_theme,
                self.used_words,
                self.previous_spy,
                round_no=self.round_no + 1,
                previous_theme=self.round.theme_id if self.round else None,
            )
        except ValueError as err:
            raise Reject(str(err)) from err
        self.previous_spy = self.round.spy_id
        self.round_no += 1
        self.deal_idx = 0
        self.revealed = False
        self.vote_idx = 0
        self.guess_actor = None
        self.guess_page = 0
        self.guess_word = None
        self.stage = Stage.DEAL_HANDOFF

    def _finish_round(self) -> None:
        deltas = self.round.settle(self.rules)
        for pid, delta in deltas.items():
            self.scores[pid] = self.scores.get(pid, 0) + delta
        self.last_deltas = deltas
        self.history.append(
            {
                "round": self.round_no,
                "spy": self.round.spy_id,
                "word": self.round.word,
                "outcome": self.round.outcome.value,
                "deltas": deltas,
            }
        )
        self.stage = Stage.RESULT

    def neutralize(self) -> None:
        if self.stage == Stage.DEAL_REVEAL:
            self.stage = Stage.DEAL_HANDOFF
        elif self.stage == Stage.VOTE_PICK:
            self.stage = Stage.VOTE_HANDOFF
        elif self.stage in (Stage.GUESS_PICK, Stage.GUESS_CONFIRM, Stage.GUESS_WHO):
            if self.round and self.round.phase == Phase.LAST_CHANCE:
                self.stage = Stage.GUESS_HANDOFF
            else:
                self.stage = self.guess_return
        self.revealed = False
        self.guess_word = None
        self.edit_idx = None
        self.seq += 1

    def to_dict(self) -> dict:
        return {
            "stage": self.stage.value,
            "seq": self.seq,
            "count": self.count,
            "names": list(self.names),
            "edit_idx": self.edit_idx,
            "themes": list(self.themes),
            "random_theme": self.random_theme,
            "scores": dict(self.scores),
            "round_no": self.round_no,
            "used_words": list(self.used_words),
            "previous_spy": self.previous_spy,
            "round": self.round.to_dict() if self.round else None,
            "deal_idx": self.deal_idx,
            "revealed": self.revealed,
            "vote_idx": self.vote_idx,
            "guess_actor": self.guess_actor,
            "guess_return": self.guess_return.value,
            "theme_page": self.theme_page,
            "guess_page": self.guess_page,
            "guess_word": self.guess_word,
            "history": list(self.history),
            "last_deltas": dict(self.last_deltas),
            "message_id": self.message_id,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict,
        library: PackLibrary | None = None,
        rng: random.Random | None = None,
    ) -> LocalSession:
        session = cls(
            library=library or load_default_library(), rng=rng or random.Random()
        )
        for key, value in data.items():
            if key == "stage":
                session.stage = Stage.DISCUSSION if value == "vote_request" else Stage(value)
            elif key == "turn_idx":
                continue
            elif key == "guess_return":
                session.guess_return = Stage(value)
            elif key == "round":
                session.round = Round.from_dict(value) if value else None
            else:
                setattr(session, key, value)
        return session
