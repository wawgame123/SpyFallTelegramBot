from __future__ import annotations

import html
import math
import random
import secrets
import time
from urllib.parse import urlencode
from dataclasses import dataclass

from app.models.game.engine import GameError, Phase, Round
from app.models.game.packs import PackLibrary, load_default_library, word_key
from app.models.game.roles import choose_spy
from app.models.game.roundfactory import create_round
from app.models.game.scoring import ScoreRules
from app.models.game.session import REASONS, Screen
from app.models.game.scoring import Outcome

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MIN_PLAYERS = 3
LAST_CHANCE_SECONDS = 30
COUNTDOWN_INTERVAL = 5
MAX_NAME_LEN = 20
MAX_MESSAGE_LEN = 200
CHAT_KEEP = 8
ROLE_OPTIONS = ["random", "spy", "civilian"]
ROLE_LABELS = {"random": "случайная", "spy": "шпион", "civilian": "обычный игрок"}
MODES = {"classic": "Классика", "hidden": "Скрытый шпион"}
SETTINGS_PHASES = ("lobby", "result", "practice_result")
PAGE_SIZE = 8
WORD_QUERY_SIZE = 64
CUSTOM_RULE_DIGITS = 52
CUSTOM_RULES = {"max": "Своё число игроков", "rounds": "Своё число раундов"}
THEMES_PAGE = 8
OPTIONS = {
    "max": ("max_players", [4, 6, 8, 10, 12]),
    "rounds": ("rounds", [3, 5, 7, 10]),
    "turn": ("turn_seconds", [30, 60, 90, 120]),
}


class RoomError(Exception):
    pass


@dataclass
class ActResult:
    alert: str | None = None
    changed: bool = True
    left: bool = False
    closed: bool = False
    penalized: bool = False
    early_guess: bool = False


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


def clean_name(raw: str) -> str:
    name = " ".join((raw or "").split())[:MAX_NAME_LEN]
    return name or "Игрок"


def esc(text: str) -> str:
    return html.escape(text, quote=False)


class Room:
    def __init__(
        self,
        code: str,
        token: str,
        host_id: int,
        title: str,
        library: PackLibrary | None = None,
        rng: random.Random | None = None,
        rules: ScoreRules | None = None,
    ):
        self.library = library or load_default_library()
        self.rng = rng or random.Random()
        self.rules = rules or ScoreRules()
        self.code = code
        self.token = token
        self.host_id = host_id
        self.title = title
        self.settings = {
            "max_players": 8,
            "rounds": 5,
            "turn_seconds": 60,
            "themes": [],
            "random_theme": False,
            "mode": "classic",
        }
        self.players: list[dict] = []
        self.scores: dict[str, int] = {}
        self.phase = "lobby"
        self.round_no = 0
        self.round: Round | None = None
        self.deadline: float | None = None
        self.chat: list[list] = []
        self.civilian_has_spoken = False
        self.used_words: list[str] = []
        self.previous_spy: str | None = None
        self.history: list[dict] = []
        self.last_deltas: dict[str, int] = {}
        self.guess_ui: dict[str, dict] = {}
        self.guess_crossed: dict[str, list[int]] = {}
        self.host_ui = "lobby"
        self.custom_rule: str | None = None
        self.themes_page = 0
        self.themes_notice = False
        self.session_id: int | None = None
        self.practice_available = False
        self.practice_done = False
        self.practice = False
        self.practice_poll: str | None = None
        self.practice_votes: dict[str, bool] = {}

    def enable_test_mode(self, size: int, admin_id: int) -> None:
        self.settings["test_size"] = size
        self.settings["test_admin"] = admin_id
        self.settings["test_role"] = "random"
        self.settings["max_players"] = size
        self.settings["min_players"] = size
        self.title = f"Тестовое лобби на {size}"

    @property
    def is_test(self) -> bool:
        return bool(self.settings.get("test_size"))

    def _p(self, uid: int) -> dict | None:
        for player in self.players:
            if player["id"] == uid:
                return player
        return None

    def _member(self, uid: int) -> dict:
        player = self._p(uid)
        if player is None:
            raise RoomError("Вы не участник этой комнаты")
        return player

    def _require_host(self, uid: int) -> None:
        self._member(uid)
        if uid != self.host_id:
            raise RoomError("Это действие доступно только хосту")

    def _require_spy(self, uid: int) -> None:
        if self.round is None or str(uid) != self.round.spy_id:
            raise RoomError("Вы не шпион: попытка недоступна")

    def name_of(self, pid: str) -> str:
        player = self._p(int(pid))
        return player["name"] if player else "?"

    def _note(self, text: str) -> None:
        self.chat.append([None, text])
        del self.chat[:-CHAT_KEEP * 3]

    def prune_missing_themes(self) -> None:
        if self.phase != "lobby":
            return
        selected = self.settings["themes"]
        available = [slug for slug in selected if self.library.has(slug)]
        if len(available) != len(selected):
            self.settings["themes"] = available
            self.themes_notice = True

    def _themes_notice(self) -> str:
        return "\n\nОдин или несколько выбранных наборов больше недоступны и удалены из выбора. Выберите замену в настройках." if self.themes_notice else ""

    def themes_label(self) -> str:
        if not self.settings["themes"]:
            return "не выбраны"
        return ", ".join(self.library.get(t).title for t in self.settings["themes"] if self.library.has(t)) or "не выбраны"

    def table(self) -> str:
        ranked = sorted(self.players, key=lambda p: (-self.scores.get(str(p["id"]), 0), p["name"]))
        return "\n".join(
            f"{i}. {esc(p['name'])} — {self.scores.get(str(p['id']), 0)}"
            for i, p in enumerate(ranked, 1)
        )

    def join(self, uid: int, name: str) -> None:
        if self._p(uid):
            return
        if self.phase == "final":
            raise RoomError("Лобби уже завершено и больше неактивно. Попросите новое приглашение.")
        between_rounds = self.phase in ("result", "practice_result") and self.host_ui != "lobby"
        if self.phase != "lobby" and not between_rounds:
            raise RoomError("Игра уже идёт. Присоединиться можно, пока комната в лобби")
        if len(self.players) >= self.settings["max_players"]:
            raise RoomError("Комната заполнена")
        base = clean_name(name)
        taken = {p["name"].casefold() for p in self.players}
        unique, i = base, 2
        while unique.casefold() in taken:
            unique = f"{base} ({i})"
            i += 1
        self.players.append(
            {"id": uid, "name": unique, "ready": uid == self.host_id, "panel": None, "seen": False}
        )
        self.scores.setdefault(str(uid), 0)
        if self.round_no or len(self.players) > 1:
            self._note(f"{unique} присоединился")

    def act(self, uid: int, action: str, now: float) -> ActResult:
        self._member(uid)
        name, _, arg = action.partition(":")
        try:
            return self._act(uid, name, arg, now)
        except GameError as err:
            raise RoomError(str(err)) from err

    def _act(self, uid: int, name: str, arg: str, now: float) -> ActResult:
        if name == "leave":
            return self._leave(uid, now)
        if name == "practice":
            self._require_host(uid)
            self._need_phase("lobby")
            if not self.practice_available or self.practice_done or self.round_no or self.is_test:
                raise RoomError("Пробный раунд сейчас недоступен")
            self._check_round_start()
            self.practice_poll = secrets.token_urlsafe(6)
            self.practice_votes = {}
            self.phase = "practice_vote"
            return ActResult()
        if name == "pv":
            return self._practice_vote(uid, arg)
        if name == "listing":
            self._require_host(uid)
            self._need_phase("lobby", "practice_result")
            if self.round_no or self.is_test:
                raise RoomError("Публичный список доступен до первого раунда")
            self.settings["listed"] = not self.settings.get("listed", False)
            return ActResult()
        if name == "rd":
            self._need_phase("lobby")
            self._member(uid)["ready"] = arg == "1"
            return ActResult()
        if name == "ui" and arg in ("lobby", "settings", "themes"):
            self._require_host(uid)
            self._need_phase(*SETTINGS_PHASES)
            self.host_ui = arg
            self.custom_rule = None
            return ActResult()
        if name == "mode" and arg in MODES:
            self._require_host(uid)
            self._need_phase(*SETTINGS_PHASES)
            self.settings["mode"] = arg
            return ActResult()
        if name == "cy" and arg == "role":
            self._require_host(uid)
            if not self.is_test:
                raise RoomError("Выбор роли доступен только в тестовом лобби")
            if self.phase not in ("lobby", "result"):
                raise RoomError("Это действие сейчас недоступно")
            current = self.settings.get("test_role", "random")
            index = ROLE_OPTIONS.index(current) if current in ROLE_OPTIONS else -1
            self.settings["test_role"] = ROLE_OPTIONS[(index + 1) % len(ROLE_OPTIONS)]
            return ActResult()
        if name == "cy" and arg in OPTIONS:
            self._require_host(uid)
            if arg == "max" and self.is_test:
                raise RoomError("В тестовом лобби число игроков фиксировано")
            self._need_phase(*SETTINGS_PHASES)
            self._cycle(arg)
            return ActResult()
        if name == "th" and self.library.has(arg):
            self._require_host(uid)
            self._need_phase(*SETTINGS_PHASES)
            self.themes_notice = False
            themes = self.settings["themes"]
            if arg in themes:
                themes.remove(arg)
            else:
                themes.append(arg)
            return ActResult()
        if name == "noop":
            return ActResult(changed=False)
        if name == "sv":
            self._require_host(uid)
            self._need_phase(*SETTINGS_PHASES)
            key, _, raw = arg.partition(":")
            if key not in OPTIONS or not raw.isascii() or not raw.isdecimal() or len(raw) > 3 or int(raw) not in OPTIONS[key][1]:
                raise RoomError("Недопустимое значение")
            value = int(raw)
            if key == "max":
                if self.is_test:
                    raise RoomError("В тестовом лобби число игроков фиксировано")
                if value < len(self.players):
                    raise RoomError("В комнате уже больше игроков")
            if key == "rounds" and value < self.round_no:
                raise RoomError("Нельзя указать меньше уже сыгранных раундов")
            self.settings[OPTIONS[key][0]] = value
            if self.custom_rule == key:
                self.custom_rule = None
            return ActResult()
        if name in ("custom", "csv"):
            self._require_host(uid)
            self._need_phase(*SETTINGS_PHASES)
            if self.host_ui != "settings":
                raise RoomError("Сначала откройте настройки комнаты")
            if name == "custom" and arg == "cancel":
                self.custom_rule = None
                return ActResult()
            key, _, raw = arg.partition(":")
            if key not in CUSTOM_RULES or (key == "max" and self.is_test):
                raise RoomError("Эта настройка сейчас недоступна")
            if name == "custom":
                self.custom_rule = key
                return ActResult()
            minimum = max(MIN_PLAYERS, len(self.players)) if key == "max" else max(1, self.round_no)
            if not raw.isascii() or not raw.isdecimal() or len(raw) > CUSTOM_RULE_DIGITS:
                raise RoomError("Отправьте целое число цифрами")
            value = int(raw)
            if value < minimum:
                if key == "max":
                    raise RoomError(f"Укажите не меньше {minimum} игроков")
                if self.round_no:
                    raise RoomError("Нельзя указать меньше уже сыгранных раундов")
                raise RoomError("Укажите хотя бы один раунд")
            self.settings[OPTIONS[key][0]] = value
            self.custom_rule = None
            return ActResult()
        if name in ("tall", "tnone"):
            self._require_host(uid)
            self._need_phase(*SETTINGS_PHASES)
            self.themes_notice = False
            self.settings["themes"] = (
                [pack.id for pack in self.library.packs()] if name == "tall" else []
            )
            return ActResult()
        if name == "tp" and arg.isdigit():
            self._require_host(uid)
            self._need_phase(*SETTINGS_PHASES)
            self.themes_page = int(arg)
            return ActResult()
        if name == "rt":
            self._require_host(uid)
            self._need_phase(*SETTINGS_PHASES)
            self.settings["random_theme"] = not self.settings["random_theme"]
            return ActResult()
        if name in ("start", "next"):
            self._require_host(uid)
            self._start_round()
            return ActResult()
        if name == "set":
            self._require_host(uid)
            self._need_phase("result", "practice_result")
            self.host_ui = "settings"
            self.custom_rule = None
            return ActResult()
        if name == "end":
            self._require_host(uid)
            self._need_phase("result")
            self.phase = "final"
            return ActResult()
        if name == "role":
            return self._role(uid, now)
        if name == "force":
            self._require_host(uid)
            self._need_phase("dealing")
            self._begin_discussion(now)
            return ActResult()
        if name == "endturn":
            self._need_playing_discussion()
            current = self.round.current()
            if str(uid) != current and uid != self.host_id:
                raise RoomError("Сейчас не ваш ход")
            event = self.round.end_turn(current, arg == "1" and str(uid) == current)
            self._apply_turn_event(event, now)
            return ActResult()
        if name == "v":
            self._need_phase("playing")
            before = self.round.phase
            self.round.cast_vote(str(uid), arg)
            self._after_event(before, now)
            return ActResult()
        if name == "gu":
            self._need_phase("playing")
            if self.round.hidden:
                if not self.round.hidden_guess_available(str(uid)):
                    raise RoomError("Попытка доступна один раз после первого круга и до голосования")
                self.guess_ui.setdefault(str(uid), {"page": 0, "word": None})
                return ActResult()
            if self.round.phase not in (
                Phase.DISCUSSION,
                Phase.SUSPECT_VOTE,
                Phase.RUNOFF_VOTE,
                Phase.LAST_CHANCE,
            ):
                raise RoomError("Это действие сейчас недоступно")
            self._require_spy(uid)
            if self.round.guesses_left <= 0:
                raise RoomError("Попытки угадать слово закончились")
            if self._is_early_guess():
                return ActResult(changed=False, early_guess=True)
            self.guess_ui.setdefault(str(uid), {"page": 0, "word": None})
            return ActResult()
        if name in ("pg", "gp", "gq", "gm", "gx", "no", "cancel", "yes", "skip"):
            return self._guess_action(uid, name, arg)
        raise RoomError("Это действие сейчас недоступно")

    def _need_phase(self, *phases: str) -> None:
        if self.phase not in phases:
            raise RoomError("Это действие сейчас недоступно")

    def _need_playing_discussion(self) -> None:
        if self.phase != "playing" or self.round is None or self.round.phase != Phase.DISCUSSION:
            raise RoomError("Это действие сейчас недоступно")

    def _cycle(self, key: str) -> None:
        field_name, options = OPTIONS[key]
        if field_name == "max_players":
            options = [o for o in options if o >= len(self.players)] or [options[-1]]
        elif field_name == "rounds":
            options = [o for o in options if o >= self.round_no]
            if not options:
                raise RoomError("Все раунды сыграны")
        current = self.settings[field_name]
        index = options.index(current) if current in options else -1
        self.settings[field_name] = options[(index + 1) % len(options)]

    def _practice_vote(self, uid: int, arg: str) -> ActResult:
        self._need_phase("practice_vote")
        poll, _, choice = arg.partition(":")
        if poll != self.practice_poll or choice not in ("1", "0", "cancel"):
            raise RoomError("Это голосование уже закрыто")
        if choice == "cancel":
            self._require_host(uid)
            self._close_practice_vote()
            return ActResult()
        self.practice_votes[str(uid)] = choice == "1"
        yes = sum(self.practice_votes.values())
        needed = len(self.players) // 2 + 1
        if yes >= needed:
            self._close_practice_vote()
            self.practice = True
            self._start_round()
        elif yes + len(self.players) - len(self.practice_votes) < needed:
            self._close_practice_vote()
            self._note("Пробный раунд не набрал большинства. Можно начать игру.")
        return ActResult()

    def _close_practice_vote(self) -> None:
        self.practice_done = True
        self.practice_poll = None
        self.practice_votes = {}
        self.phase = "lobby"

    def _check_round_start(self) -> None:
        if self.phase not in ("lobby", "result", "practice_result"):
            raise RoomError("Раунд уже идёт")
        if self.round_no >= self.settings["rounds"]:
            raise RoomError("Все раунды сыграны. Завершите игру")
        minimum = self.settings.get("min_players", MIN_PLAYERS)
        if len(self.players) < minimum:
            raise RoomError(f"Недостаточно игроков: нужно минимум {minimum}")
        if not self.settings["themes"]:
            raise RoomError("Выберите хотя бы одну тему в настройках")
        if self.round_no == 0 and any(not p["ready"] for p in self.players):
            raise RoomError("Не все игроки готовы")

    def _start_round(self) -> None:
        self._check_round_start()
        pids = [str(p["id"]) for p in self.players]
        spy_override = self._forced_spy(pids)
        try:
            self.round, self.used_words = create_round(
                self.library,
                self.rng,
                pids,
                self.settings["themes"],
                self.settings["random_theme"],
                self.used_words,
                self.previous_spy,
                spy_override=spy_override,
                round_no=self.round_no + 1,
                previous_theme=self.round.theme_id if self.round else None,
                open_guess=True,
                hidden=self.settings.get("mode") == "hidden",
            )
        except ValueError as err:
            raise RoomError(str(err)) from err
        self.previous_spy = self.round.spy_id
        if not self.practice:
            self.round_no += 1
        self.phase = "dealing"
        self.deadline = None
        self.guess_ui = {}
        self.guess_crossed = {}
        self.host_ui = "lobby"
        self.custom_rule = None
        self.chat = []
        for player in self.players:
            player["seen"] = False
            self.scores.setdefault(str(player["id"]), 0)

    def _forced_spy(self, pids: list[str]) -> str | None:
        if not self.is_test:
            return None
        role = self.settings.get("test_role", "random")
        admin = str(self.settings.get("test_admin"))
        if role == "random" or admin not in pids:
            return None
        if role == "spy":
            return admin
        others = [p for p in pids if p != admin]
        if not others:
            raise RoomError("В лобби на 1 игрока возможна только роль шпиона или случайная")
        return choose_spy(others, self.previous_spy, self.rng)

    def _role_label(self) -> str:
        return ROLE_LABELS.get(self.settings.get("test_role", "random"), "случайная")

    def _role(self, uid: int, now: float) -> ActResult:
        if self.phase not in ("dealing", "playing") or self.round is None:
            raise RoomError("Роль сейчас недоступна")
        if self.round.phase == Phase.FINISHED:
            raise RoomError("Раунд завершён")
        player = self._member(uid)
        first = not player["seen"]
        player["seen"] = True
        if self.round.hidden:
            text = f"Ваше слово:\n\n{self.round.word_for(str(uid)).upper()}"
        elif str(uid) == self.round.spy_id:
            text = "Вы шпион! Определите загаданное слово."
        else:
            text = f"Загаданное слово:\n\n{self.round.word.upper()}"
        changed = False
        if self.phase == "dealing":
            changed = first
            if all(p["seen"] for p in self.players):
                self._begin_discussion(now)
                changed = True
        return ActResult(alert=text, changed=changed)

    def _begin_discussion(self, now: float) -> None:
        self.round.finish_deal()
        self.phase = "playing"
        self.deadline = now + self.settings["turn_seconds"]
        self.chat = []
        self.civilian_has_spoken = False
        self._note("Обсуждение началось")

    def current(self) -> str:
        return self.round.current()

    def _apply_turn_event(self, event: str, now: float) -> None:
        if self.round.phase == Phase.FINISHED:
            self._finish_round()
            return
        if event == "vote":
            if self.round.hidden:
                self.guess_ui.clear()
            self.deadline = None
            self._note("Круг завершён: большинство за голосование. Голосование началось")
            return
        self.deadline = now + self.settings["turn_seconds"]
        if event == "new_circle":
            self._note(f"Круг {self.round.circle_no - 1} завершён: голосования не будет")



    def tick(self, now: float) -> bool:
        if (
            self.phase != "playing"
            or self.round is None
            or self.deadline is None
            or now < self.deadline
        ):
            return False
        if self.round.phase == Phase.DISCUSSION:
            event = self.round.end_turn(self.round.current(), False)
            self._apply_turn_event(event, now)
            return True
        if self.round.phase == Phase.LAST_CHANCE:
            self.round.skip_last_chance()
            self._finish_round()
            return True
        return False

    def say(self, uid: int, text: str, now: float, *, expected_phase: str | None = None) -> None:
        player = self._member(uid)
        if self.phase == "lobby":
            phase = "lobby"
        elif self.phase == "playing" and self.round is not None and self.round.phase == Phase.DISCUSSION:
            phase = "discussion"
        else:
            raise RoomError("Сейчас нельзя писать в чат")
        if expected_phase is not None and expected_phase != phase:
            raise RoomError("Чат изменился. Отправьте сообщение ещё раз.")
        if phase == "discussion" and str(uid) != self.round.current():
            raise RoomError("Писать можно только в свой ход")
        cleaned = "\n".join(" ".join(line.split()) for line in text.splitlines()).strip()
        if not cleaned:
            raise RoomError("Пустое сообщение")
        if len(cleaned) > MAX_MESSAGE_LEN:
            raise RoomError(f"Слишком длинное сообщение (максимум {MAX_MESSAGE_LEN} символов)")
        if phase == "discussion" and str(uid) != self.round.spy_id:
            self.civilian_has_spoken = True
        self.chat.append([player["name"], cleaned, uid])
        del self.chat[:-CHAT_KEEP * 3]

    def _after_event(self, phase_before: Phase, now: float) -> None:
        phase = self.round.phase
        if phase == Phase.FINISHED:
            self._finish_round()
        elif phase == Phase.LAST_CHANCE:
            self.guess_ui = {self.round.spy_id: {"page": 0, "word": None}}
            self.deadline = now + LAST_CHANCE_SECONDS
        elif phase == Phase.RUNOFF_VOTE and phase_before != Phase.RUNOFF_VOTE:
            self._note("Ничья: дополнительное голосование")

    def _is_early_guess(self) -> bool:
        return (
            not self.round.hidden
            and not self.is_test
            and self.round.phase == Phase.DISCUSSION
            and not self.civilian_has_spoken
        )

    def _guess_words(self) -> list[str]:
        return sorted(self.round.allowed_words, key=str.casefold)

    def _guess_panel(self, uid: int) -> dict | None:
        if self.phase != "playing" or self.round is None:
            return None
        me = str(uid)
        allowed = self.round.hidden_guess_available(me) if self.round.hidden else (
            self.round.spy_id == me and self.round.guesses_left > 0
        )
        return self.guess_ui.get(me) if allowed else None

    def guess_sheet(self, uid: int) -> dict | None:
        panel = self._guess_panel(uid)
        if panel is None or panel.get("word"):
            return None
        return {
            "words": self._guess_words(),
            "crossed": self.guess_crossed.get(str(uid), []),
            "round": self.round_no,
            "practice": self.practice,
            "hint": self._guess_hint(),
        }

    def allows_web_guess(self, uid: int, action: str) -> bool:
        name, _, _ = action.partition(":")
        if name not in ("gp", "gx"):
            return False
        panel = self._guess_panel(uid)
        return panel is not None and not panel.get("word")

    def setting_input(self, uid: int) -> str | None:
        if uid == self.host_id and self.phase in SETTINGS_PHASES and self.host_ui == "settings":
            return self.custom_rule
        return None

    def _guess_action(self, uid: int, name: str, arg: str) -> ActResult:
        self._need_phase("playing")
        me = str(uid)
        ui = self.guess_ui.get(me)
        if ui is None:
            raise RoomError("Панель угадывания закрыта")
        if self.round.hidden and not self.round.hidden_guess_available(me):
            raise RoomError("Попытка доступна один раз после первого круга и до голосования")
        if name in ("gp", "yes") and self._is_early_guess():
            self._require_spy(uid)
            return ActResult(changed=False, early_guess=True)
        last_chance = self.round.phase == Phase.LAST_CHANCE
        words = self._guess_words()
        crossed = self.guess_crossed.get(me, [])
        if name in ("gq", "gm", "gx", "gp") and (self._guess_panel(uid) is None or ui.get("word")):
            raise RoomError("Вернитесь к списку слов")
        if name == "gq":
            if len(arg) > WORD_QUERY_SIZE:
                raise RoomError(f"Поиск принимает до {WORD_QUERY_SIZE} символов")
            ui["query"] = " ".join(arg.split())
            ui["page"] = 0
            return ActResult()
        if name == "gm":
            ui["marking"] = not ui.get("marking", False)
            ui["page"] = 0
            return ActResult()
        if name == "gx":
            raw, _, value = arg.partition(":")
            if value not in ("0", "1") or not raw.isascii() or not raw.isdecimal() or len(raw) > 4:
                raise RoomError("Выберите слово из списка")
            index = int(raw)
            if index >= len(words):
                raise RoomError("Выберите слово из списка")
            marked = set(crossed)
            if value == "1":
                marked.add(index)
            else:
                marked.discard(index)
            if marked:
                self.guess_crossed[me] = sorted(marked)
            else:
                self.guess_crossed.pop(me, None)
            return ActResult()
        if name == "pg" and arg.isascii() and arg.isdecimal() and len(arg) <= 4:
            ui["page"] = int(arg)
            return ActResult()
        if name == "gp" and arg.isascii() and arg.isdecimal() and len(arg) <= 4 and int(arg) < len(words):
            index = int(arg)
            if index in crossed:
                raise RoomError("Сначала верните вычеркнутое слово в список")
            ui["word"] = words[index]
            return ActResult()
        if name == "no":
            ui["word"] = None
            return ActResult()
        if name == "cancel":
            if last_chance:
                raise RoomError("Сейчас можно только угадать слово или отказаться")
            self.guess_ui.pop(me, None)
            return ActResult()
        if name == "skip":
            if not last_chance or me != self.round.spy_id:
                raise RoomError("Это действие сейчас недоступно")
            self.round.skip_last_chance()
            self._finish_round()
            return ActResult()
        if name == "yes":
            word = ui.get("word")
            if not word:
                raise RoomError("Сначала выберите слово")
            self.guess_ui.pop(me, None)
            if not self.round.hidden:
                self._require_spy(uid)
            correct = self.round.guess(me, word)
            if self.round.phase == Phase.FINISHED:
                self._finish_round()
                return ActResult()
            if self.round.hidden:
                return ActResult(alert="Попытка принята. Игра продолжается.")
            return ActResult(alert="Верно!" if correct else "Неверно. Попытка использована.")
        raise RoomError("Это действие сейчас недоступно")

    def _finish_round(self) -> None:
        self.guess_crossed = {}
        if self.practice:
            self.practice = False
            self.phase = "practice_result"
            self.deadline = None
            self.guess_ui = {}
            return
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
                "names": {str(p["id"]): p["name"] for p in self.players},
            }
        )
        self.phase = "result"
        self.deadline = None
        self.guess_ui = {}

    def _cancel_round(self) -> None:
        self.round = None
        self.phase = "lobby"
        if not self.practice:
            self.round_no = max(0, self.round_no - 1)
        self.practice = False
        self.deadline = None
        self.guess_ui = {}
        self.host_ui = "lobby"
        self.custom_rule = None
        self.guess_crossed = {}
        for other in self.players:
            other["ready"] = True
            other["seen"] = False

    def _apply_leave_event(self, event: str, now: float) -> None:
        if self.round.phase == Phase.FINISHED:
            self._finish_round()
            return
        if self.phase == "dealing":
            if all(p["seen"] for p in self.players):
                self._begin_discussion(now)
            return
        if event == "vote":
            if self.round.hidden:
                self.guess_ui.clear()
            self.deadline = None
            self._note("Круг завершён: большинство за голосование. Голосование началось")
        elif self.round.phase == Phase.DISCUSSION:
            self.deadline = now + self.settings["turn_seconds"]

    def _leave(self, uid: int, now: float) -> ActResult:
        player = self._member(uid)
        pid = str(uid)
        name = player["name"]
        active = self.phase in ("dealing", "playing") and self.round is not None
        penalized = active and not (self.is_test or self.practice)
        if self.phase == "practice_vote":
            self._close_practice_vote()
            self.practice_done = False
            self._note("Состав комнаты изменился. Голосование отменено.")
        self.guess_ui.pop(pid, None)
        self.guess_crossed.pop(pid, None)
        note = f"{name} вышел"
        removed = False
        if active:
            remaining = len(self.players) - 1
            minimum = self.settings.get("min_players", MIN_PLAYERS)
            if pid == self.round.spy_id:
                self.round.spy_left()
                self._finish_round()
                note = f"{name} (шпион) покинул игру, победили игроки"
            elif remaining < minimum:
                self.players.remove(player)
                removed = True
                self._cancel_round()
                note = f"{name} вышел, раунд отменён"
            else:
                self.players.remove(player)
                removed = True
                event = self.round.remove_player(pid)
                self._apply_leave_event(event, now)
                note = f"{name} вышел из игры"
        if not removed:
            self.players.remove(player)
        if not self.players:
            return ActResult(left=True, closed=True, penalized=penalized)
        if uid == self.host_id:
            self.host_id = self.players[0]["id"]
            self._note(f"Новый хост: {self.players[0]['name']}")
        self._note(note)
        return ActResult(left=True, penalized=penalized)

    def link(self, bot_username: str) -> str:
        return f"https://t.me/{bot_username.removeprefix('@')}?start=join_{self.token}"

    def view_for(self, uid: int, bot_username: str = "", now: float | None = None) -> Screen:
        now = time.time() if now is None else now
        self._member(uid)
        if uid == self.host_id and self.phase in SETTINGS_PHASES:
            if self.host_ui == "settings":
                return self._settings_view()
            if self.host_ui == "themes":
                return self._themes_view()
        if self.phase == "lobby":
            return self._lobby_view(uid, bot_username)
        if self.phase == "dealing":
            return self._dealing_view(uid)
        if self.phase == "playing":
            return self._playing_view(uid, now)
        if self.phase == "result":
            return self._result_view(uid)
        if self.phase == "practice_vote":
            return self._practice_vote_view(uid)
        if self.phase == "practice_result":
            return self._practice_result_view(uid)
        rows = [[("В меню", "leave")]]
        if uid == self.host_id:
            rows.insert(0, [("Реванш", f"rematch:{self.code}")])
        return Screen(
            f"<b>🏁 Игра завершена!</b>\n\n<b>Итоговая таблица</b>\n{self.table()}",
            rows,
            html=True,
        )

    def _chat_text(self) -> str:
        lines = []
        for author, text, *_ in self.chat[-CHAT_KEEP:]:
            lines.append(f"• {esc(text)}" if author is None else f"<b>{esc(author)}:</b> {esc(text)}")
        return "\n".join(lines)

    def _lobby_view(self, uid: int, bot_username: str) -> Screen:
        host = uid == self.host_id
        lines = []
        for p in self.players:
            crown = " 👑" if p["id"] == self.host_id else ""
            status = "🟢 готов" if p["ready"] else "⚪ ожидает"
            lines.append(f"{status} · {esc(p['name'])}{crown}")
        text = (
            f"<b>🎮 {esc(self.title)}</b>\n\n"
            f"🔑 Код комнаты: <code>{self.code}</code>\n"
        )
        if bot_username:
            text += f'<a href="{esc(self.link(bot_username))}">Ссылка</a>\n'
        text += (
            f"\n<b>👥 Игроки · {len(self.players)}/{self.settings['max_players']}</b>"
            f" · готовы {sum(1 for p in self.players if p['ready'])}/{len(self.players)}\n"
            + "\n".join(lines)
            + (f"\n\n🧪 Тестовый режим · роль хоста: {self._role_label()}" if self.is_test else "")
            + f"\n\n<b>🎯 Темы:</b> {esc(self.themes_label())}{self._order_suffix()}"
            + f"\n<b>Режим:</b> {MODES[self.settings.get('mode', 'classic')]}"
            + f"\n<b>⚙️ Настройки:</b> {self.settings['rounds']} раундов · {self.settings['turn_seconds']} с на ход"
        )
        text += self._themes_notice()
        if self.round_no:
            text += f"\n\n<b>🏆 Результаты после {self.round_no} раундов</b>\n{self.table()}"
        if self.chat:
            text += f"\n\n<b>Чат комнаты</b>\n{self._chat_text()}"
        rows: list[list[tuple[str, str]]] = []
        if host:
            rows.append([("Начать игру", "start")])
            if self.practice_available and not self.practice_done and not self.round_no:
                rows.append([("Предложить пробный раунд", "practice")])
            if self.is_test:
                rows.append([(f"Моя роль: {self._role_label()}", "cy:role")])
            rows.append([("Настройки", "ui:settings")])
        else:
            me = self._p(uid)
            rows.append([("Не готов", "rd:0")] if me["ready"] else [("Готов", "rd:1")])
        if bot_username:
            invitation = (
                "🕵️ Приглашаю сыграть в «ШПИОН»!\n"
                f"Комната: {self.title}\n"
                f"Код комнаты: {self.code}\n"
                "Открой ссылку и присоединяйся к игре."
            )
            share_url = "https://t.me/share/url?" + urlencode({
                "url": self.link(bot_username), "text": invitation,
            })
            rows.append([
                ("Пригласить", f"url:{share_url}"),
                ("Скопировать ссылку", f"copy:{self.link(bot_username)}"),
            ])
        rows.append([("Выйти", "leave")])
        return Screen(text, rows, html=True)

    def _option_rows(self, title: str, key: str) -> list[list[tuple[str, str]]]:
        field_name, options = OPTIONS[key]
        current = self.settings[field_name]
        buttons = []
        for value in options:
            if (key == "max" and value < len(self.players)) or (key == "rounds" and value < self.round_no):
                continue
            label = f"[x] {value}" if value == current else str(value)
            buttons.append((label, f"sv:{key}:{value}"))
        rows = [[(title, "noop")]]
        if buttons:
            rows.append(buttons)
        if key in CUSTOM_RULES:
            rows.append([(CUSTOM_RULES[key], f"custom:{key}")])
        return rows

    def _order_label(self) -> str:
        return "случайный" if self.settings["random_theme"] else "по очереди"

    def _order_suffix(self) -> str:
        if len(self.settings["themes"]) > 1:
            return f" (порядок: {self._order_label()})"
        return ""

    def _settings_view(self) -> Screen:
        rows: list[list[tuple[str, str]]] = []
        mode = self.settings.get("mode", "classic")
        rows.append([(f"{'[x] ' if key == mode else ''}{label}", f"mode:{key}") for key, label in MODES.items()])
        if not self.is_test:
            rows += self._option_rows("Игроков в комнате", "max")
        rows += self._option_rows("Раундов в игре", "rounds")
        rows += self._option_rows("Секунд на ход", "turn")
        if not self.is_test and not self.round_no:
            visibility = "в общем списке" if self.settings.get("listed") else "по приглашению"
            rows.append([(f"Видимость: {visibility}", "listing")])
        rows += [
            [("Темы: выбрать", "ui:themes")],
            [(f"Порядок тем: {self._order_label()}", "rt")],
            [("Готово, назад в лобби" if self.phase == "lobby" else "Готово, к итогам", "ui:lobby")],
        ]
        custom_note = ""
        if self.custom_rule:
            label = "игроков" if self.custom_rule == "max" else "раундов"
            custom_note = f"\n\nОтправьте число {label} одним сообщением."
            rows.insert(-1, [("Отменить ввод", "custom:cancel")])
        hint = (
            "\nШпион получает другое слово из того же набора. После первого круга любой игрок "
            "может сделать одну попытку до голосования. Мирный, сделавший попытку, не получит очки."
            if mode == "hidden" else ""
        )
        return Screen(
            "<b>Настройки комнаты</b>\n\nНажмите на нужное значение, оно сохранится сразу.\n"
            f"Режим: {MODES[mode]}\nТемы сейчас: {esc(self.themes_label())}{self._order_suffix()}"
            + f"\nДо {self.settings['max_players']} игроков · {self.settings['rounds']} раундов"
            + hint + custom_note + self._themes_notice(),
            rows,
            html=True,
        )

    def _themes_view(self) -> Screen:
        rows = []
        all_packs = self.library.packs()
        pages = max(1, -(-len(all_packs) // THEMES_PAGE))
        page = min(self.themes_page, pages - 1)
        for pack in all_packs[page * THEMES_PAGE : (page + 1) * THEMES_PAGE]:
            mark = "[x]" if pack.id in self.settings["themes"] else "[ ]"
            rows.append([(f"{mark} {pack.title} ({len(pack.words)})", f"th:{pack.id}")])
        nav = []
        if page > 0:
            nav.append(("Назад", f"tp:{page - 1}"))
        if page < pages - 1:
            nav.append(("Вперёд", f"tp:{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([("Выбрать все", "tall"), ("Очистить", "tnone")])
        rows.append([(f"Порядок тем: {self._order_label()}", "rt")])
        rows.append([("Готово", "ui:settings")])
        return Screen(
            "<b>Темы игры</b>\n\nОтметьте одну или несколько. "
            "В каждом раунде играет одна тема из отмеченных: по очереди или случайно.",
            rows,
            html=True,
        )

    def _round_heading(self) -> str:
        title = "Пробный раунд" if self.practice else f"Раунд {self.round_no} из {self.settings['rounds']}"
        return title + " · Скрытый шпион" if self.round and self.round.hidden else title

    def _practice_vote_view(self, uid: int) -> Screen:
        yes = sum(self.practice_votes.values())
        needed = len(self.players) // 2 + 1
        mine = self.practice_votes.get(str(uid))
        poll = self.practice_poll
        rows = [[("[x] За" if mine is True else "За", f"pv:{poll}:1"),
                 ("[x] Против" if mine is False else "Против", f"pv:{poll}:0")]]
        if uid == self.host_id:
            rows.append([("Отменить голосование", f"pv:{poll}:cancel")])
        rows.append([("Выйти", "leave")])
        return Screen(
            "<b>🗳 Сыграть пробный раунд?</b>\n\n"
            "Потренируйтесь с текущим составом без начисления очков.\n\n"
            f"<b>За:</b> {yes} · нужно {needed}\n"
            f"<b>Проголосовали:</b> {len(self.practice_votes)}/{len(self.players)}",
            rows, html=True,
        )

    def _practice_result_view(self, uid: int) -> Screen:
        rnd = self.round
        rows = [[("Выйти", "leave")]]
        text = (
            "<b>🏁 Пробный раунд завершён</b>\n\n"
            f"Шпион: <b>{esc(self.name_of(rnd.spy_id))}</b>\n"
            f"Слово: <b>{esc(rnd.word)}</b>\n{REASONS[rnd.outcome]}\n\n"
            "Теперь можно начать игру на очки."
        )
        if uid == self.host_id:
            rows.insert(0, [("Начать игру", "start"), ("Изменить настройки", "set")])
        return Screen(text, rows, html=True)

    def _dealing_view(self, uid: int) -> Screen:
        seen = sum(1 for p in self.players if p["seen"])
        lines = "\n".join(
            f"{'✅' if p['seen'] else '○'} {esc(p['name'])}" for p in self.players
        )
        label = "Показать слово" if self.round.hidden else "Показать роль"
        rows = [[(label, "role")]]
        if uid == self.host_id:
            rows.append([("Начать обсуждение", "force")])
        rows.append([("Выйти", "leave")])
        practice_note = (
            "\n\nОбычные игроки знают слово и дают подсказки. "
            "Шпион слушает подсказки и пытается угадать слово."
            if self.practice and not self.round.hidden else ""
        )
        return Screen(
            f"<b>{self._round_heading()}</b> · выдача ролей\n\n"
            f"Нажмите «{label}», чтобы открыть карточку во всплывающем окне."
            f"{practice_note}"
            f"\n\n<b>Посмотрели:</b> {seen} из {len(self.players)}\n{lines}",
            rows,
            html=True,
        )

    def _playing_view(self, uid: int, now: float) -> Screen:
        rnd = self.round
        me = str(uid)
        head = (
            f"<b>{self._round_heading()}</b> · "
            f"{esc(rnd.theme)}"
        )
        if rnd.phase == Phase.LAST_CHANCE:
            if me == rnd.spy_id:
                return self._guess_view(head, me, now)
            return Screen(
                f"{head}\n\nШпион определён: <b>{esc(self.name_of(rnd.spy_id))}</b>.\n"
                "У него последний шанс угадать слово. "
                f"Осталось около {self._left_seconds(now, LAST_CHANCE_SECONDS)} с. Если не успеет, победят игроки.",
                [[("Моя роль", "role")], [("Выйти", "leave")]],
                html=True,
            )
        if me in self.guess_ui:
            return self._guess_view(head, me, now)
        if rnd.phase in (Phase.SUSPECT_VOTE, Phase.RUNOFF_VOTE):
            return self._vote_view(head, me)
        return self._discussion_view(head, me, now)

    def _discussion_view(self, head: str, me: str, now: float) -> Screen:
        rnd = self.round
        order = rnd.order
        idx = rnd.turn_idx % len(order)
        current = rnd.current()
        left = self._left_seconds(now, self.settings["turn_seconds"])
        listing = " → ".join(
            f"<b>{esc(self.name_of(pid))}</b>" if i == idx else esc(self.name_of(pid))
            for i, pid in enumerate(order)
        )
        text = (
            f"{head}\n\n<b>Обсуждение · круг {rnd.circle_no}</b>\n"
            f"🎙 Сейчас говорит: <b>{esc(self.name_of(current))}</b> · ⏳ Осталось около {left} с\n"
            f"Далее: {esc(self.name_of(rnd.next_player()))}"
        )
        if me == current:
            text += "\n\n<b>Ваш ход!</b> Напишите подсказку в чат."
        text += f"\n\n{listing}\nГолосование начнётся после круга, если его предложит большинство."
        if rnd.hidden and rnd.circle_no == 1:
            text += "\nУгадывать слово можно после первого круга, если обсуждение продолжится."
        if self.chat:
            text += f"\n\n<b>Чат комнаты</b>\n{self._chat_text()}"
        rows: list[list[tuple[str, str]]] = []
        if me == current:
            rows.append([("Закончить ход", "endturn:0")])
            rows.append([("Закончить ход и предложить голосование", "endturn:1")])
        elif me == str(self.host_id):
            rows.append([("Пропустить ход игрока", "endturn:0")])
        role = ("Моё слово" if rnd.hidden else "Моя роль", "role")
        rows.append([("Угадать слово", "gu"), role] if not rnd.hidden or rnd.hidden_guess_available(me) else [role])
        rows.append([("Выйти", "leave")])
        return Screen(text, rows, html=True)

    def _vote_view(self, head: str, me: str) -> Screen:
        rnd = self.round
        note = ""
        if rnd.phase == Phase.RUNOFF_VOTE:
            names = ", ".join(esc(self.name_of(p)) for p in rnd.candidates)
            note = f"\nДополнительное голосование между: {names}\n"
        chosen = rnd.votes.get(me)
        rows = []
        for target in rnd.valid_targets(me):
            mark = "[x] " if target == chosen else ""
            rows.append([(f"{mark}{self.name_of(target)}", f"v:{target}")])
        rows.append([("Моё слово", "role")] if rnd.hidden else [("Угадать слово", "gu"), ("Моя роль", "role")])
        rows.append([("Выйти", "leave")])
        status = "Ваш голос принят, можно изменить до конца." if chosen else "Кого вы считаете шпионом?"
        return Screen(
            f"{head}\n\n<b>🗳 Тайное голосование</b>{note}\n"
            f"Проголосовали: {len(rnd.votes)} из {len(rnd.players)}\n\n{status}",
            rows,
            html=True,
        )

    def _left_seconds(self, now: float, limit: int) -> int:
        remaining = max(0.0, (self.deadline or now) - now)
        return min(limit, math.ceil(remaining / COUNTDOWN_INTERVAL) * COUNTDOWN_INTERVAL)

    def _guess_hint(self) -> str:
        if self.round.hidden:
            return "Выберите слово большинства. Попытка одна."
        if self.round.phase == Phase.LAST_CHANCE:
            return "Последний шанс! Выберите слово."
        if self.round.phase == Phase.DISCUSSION:
            return "Выберите слово. Попытка одна: верный ответ даёт вам победу, ошибка означает поражение."
        return (
            "Выберите слово. Если угадаете, будет ничья при раскрытии шпиона или ваша победа, "
            "если вас не раскроют. Ошибка сжигает попытку."
        )

    def _guess_view(self, head: str, me: str, now: float | None = None) -> Screen:
        ui = self.guess_ui.get(me) or {"page": 0, "word": None}
        words = self._guess_words()
        last_chance = self.round.phase == Phase.LAST_CHANCE
        word = ui.get("word")
        if word:
            if self.round.hidden:
                hint = "Если вы мирный, попытка лишит вас очков при победе."
            elif self.round.phase == Phase.DISCUSSION:
                hint = "Ошибка сейчас означает поражение."
            elif last_chance:
                hint = "Верный ответ даст ничью."
            else:
                hint = "Итог станет известен после голосования."
            return Screen(
                f"{head}\n\nВы выбрали: <b>{esc(word)}</b>\n\nОтправить ответ? {hint}",
                [[("Да", "yes"), ("Другое слово", "no")]],
                html=True,
            )
        crossed = set(self.guess_crossed.get(me, ()))
        marking = ui.get("marking", False)
        query = ui.get("query", "")
        needle = word_key(query)
        matches = [
            (index, word) for index, word in enumerate(words)
            if (marking or index not in crossed) and needle in word_key(word)
        ]
        pages = max(1, -(-len(matches) // PAGE_SIZE))
        page = min(ui.get("page", 0), pages - 1)
        chunk = matches[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
        rows = []
        options = [
            (("↶ " + label, f"gx:{index}:0") if index in crossed else ("− " + label, f"gx:{index}:1"))
            if marking else (label, f"gp:{index}")
            for index, label in chunk
        ]
        rows.extend(options[i:i + 2] for i in range(0, len(options), 2))
        nav = []
        if page > 0:
            nav.append(("Назад", f"pg:{page - 1}"))
        if page < pages - 1:
            nav.append(("Вперёд", f"pg:{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([("К выбору ответа" if marking else "Вычеркнуть слова", "gm")])
        if last_chance:
            rows.append([("Не угадывать", "skip")])
        else:
            rows.append([("Отмена", "cancel")])
        left = (
            f" Осталось около {self._left_seconds(now, LAST_CHANCE_SECONDS)} с."
            if last_chance and now is not None
            else ""
        )
        title = "Нажмите на слово, чтобы вычеркнуть или вернуть его." if marking else self._guess_hint()
        filtered = f"\nПоиск: <b>{esc(query)}</b>" if query else ""
        empty = "\nПодходящих слов нет." if not matches else ""
        status = f"\nВычеркнуто: {len(crossed)}" if crossed else ""
        return Screen(
            f"{head}\n\n{title}{left}\nСтраница {page + 1}/{pages}{filtered}{empty}{status}"
            "\n\nПоиск: <code>/word часть слова</code>. Сброс: <code>/word</code>.",
            rows, html=True,
        )

    def _secret_guess_line(self) -> str:
        rnd = self.round
        if rnd is None or not rnd.secret_guess_word:
            return ""
        verdict = "верно" if rnd.secret_guess_correct else "неверно"
        return f"\n\nПопытка шпиона: {esc(rnd.secret_guess_word)} ({verdict})"

    def _result_view(self, uid: int) -> Screen:
        info = self.history[-1]
        lines = "\n".join(
            f"{esc(p['name'])}: +{info['deltas'].get(str(p['id']), 0)}" for p in self.players
        )
        text = (
            f"<b>🏁 Итоги раунда {info['round']}</b>\n\n"
            f"Шпион: <b>{esc(self.name_of(info['spy']))}</b>\n"
            f"Слово: <b>{esc(info['word'])}</b>\n"
            f"{REASONS[Outcome(info['outcome'])]}\n\n"
            f"<b>Очки за раунд</b>\n{lines}\n\n<b>Общий счёт</b>\n{self.table()}"
        )
        text += self._secret_guess_line()
        if self.round and self.round.hidden:
            text += f"\n\nСлово шпиона: <b>{esc(self.round.hidden_spy_word)}</b>"
        rows: list[list[tuple[str, str]]] = []
        if uid == self.host_id:
            if self.round_no < self.settings["rounds"]:
                rows.append([("Следующий раунд", "next")])
                if self.is_test:
                    rows.append([(f"Моя роль: {self._role_label()}", "cy:role")])
            rows.append([("Изменить настройки", "set")])
            rows.append([("Завершить игру", "end")])
        else:
            text += "\n\nЖдём решения хоста."
        rows.append([("Выйти", "leave")])
        return Screen(text, rows, html=True)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "token": self.token,
            "host_id": self.host_id,
            "title": self.title,
            "settings": dict(self.settings),
            "players": [dict(p) for p in self.players],
            "scores": dict(self.scores),
            "phase": self.phase,
            "round_no": self.round_no,
            "round": self.round.to_dict() if self.round else None,
            "deadline": self.deadline,
            "chat": [list(c) for c in self.chat],
            "civilian_has_spoken": self.civilian_has_spoken,
            "used_words": list(self.used_words),
            "previous_spy": self.previous_spy,
            "history": list(self.history),
            "last_deltas": dict(self.last_deltas),
            "guess_ui": {k: dict(v) for k, v in self.guess_ui.items()},
            "guess_crossed": {k: list(v) for k, v in self.guess_crossed.items()},
            "host_ui": self.host_ui,
            "custom_rule": self.custom_rule,
            "themes_page": self.themes_page,
            "themes_notice": self.themes_notice,
            "session_id": self.session_id,
            "practice_done": self.practice_done,
            "practice": self.practice,
            "practice_poll": self.practice_poll,
            "practice_votes": dict(self.practice_votes),
        }

    @classmethod
    def from_dict(
        cls, data: dict, library: PackLibrary | None = None, rng: random.Random | None = None,
        *, prune_themes: bool = True,
    ) -> Room:
        room = cls(data["code"], data["token"], data["host_id"], data["title"], library, rng)
        for key, value in data.items():
            if key == "round":
                room.round = Round.from_dict(value) if value else None
            elif key == "settings":
                room.settings.update(value)
            elif key not in ("code", "token", "host_id", "title", "turn_idx", "guess_ui"):
                setattr(room, key, value)
        if "civilian_has_spoken" not in data and room.round is not None:
            spy_name = room.name_of(room.round.spy_id)
            room.civilian_has_spoken = any(
                (sender[0] != int(room.round.spy_id) if sender else author != spy_name)
                for author, _, *sender in room.chat if author is not None
            )
        stored = data.get("guess_ui")
        if isinstance(stored, dict) and "page" not in stored:
            room.guess_ui = {str(k): dict(v) for k, v in stored.items()}
        if prune_themes:
            room.prune_missing_themes()
        return room
