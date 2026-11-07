from __future__ import annotations

import html
import math
import random
import secrets
import time
from urllib.parse import urlencode
from dataclasses import dataclass

from app.game.engine import GameError, Phase, Round
from app.game.packs import PackLibrary, load_default_library
from app.game.roles import choose_spy
from app.game.roundfactory import create_round
from app.game.scoring import ScoreRules
from app.game.session import REASONS, Screen
from app.game.scoring import Outcome

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MIN_PLAYERS = 3
LAST_CHANCE_SECONDS = 30
MAX_NAME_LEN = 20
MAX_MESSAGE_LEN = 200
CHAT_KEEP = 8
ROLE_OPTIONS = ["random", "spy", "civilian"]
ROLE_LABELS = {"random": "случайная", "spy": "шпион", "civilian": "обычный игрок"}
PAGE_SIZE = 8
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
        }
        self.players: list[dict] = []
        self.scores: dict[str, int] = {}
        self.phase = "lobby"
        self.round_no = 0
        self.round: Round | None = None
        self.deadline: float | None = None
        self.chat: list[list] = []
        self.used_words: list[str] = []
        self.previous_spy: str | None = None
        self.history: list[dict] = []
        self.last_deltas: dict[str, int] = {}
        self.guess_ui: dict[str, dict] = {}
        self.host_ui = "lobby"
        self.themes_page = 0
        self.themes_notice = False
        self.session_id: int | None = None

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
        if self.phase != "lobby":
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
        if name == "rd":
            self._need_phase("lobby")
            self._member(uid)["ready"] = arg == "1"
            return ActResult()
        if name == "ui" and arg in ("lobby", "settings", "themes"):
            self._require_host(uid)
            self._need_phase("lobby")
            self.host_ui = arg
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
            self._need_phase("lobby")
            self._cycle(arg)
            return ActResult()
        if name == "th" and self.library.has(arg):
            self._require_host(uid)
            self._need_phase("lobby")
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
            self._need_phase("lobby")
            key, _, raw = arg.partition(":")
            if key not in OPTIONS or not raw.isdigit() or int(raw) not in OPTIONS[key][1]:
                raise RoomError("Недопустимое значение")
            value = int(raw)
            if key == "max":
                if self.is_test:
                    raise RoomError("В тестовом лобби число игроков фиксировано")
                if value < len(self.players):
                    raise RoomError("В комнате уже больше игроков")
            self.settings[OPTIONS[key][0]] = value
            return ActResult()
        if name in ("tall", "tnone"):
            self._require_host(uid)
            self._need_phase("lobby")
            self.themes_notice = False
            self.settings["themes"] = (
                [pack.id for pack in self.library.packs()] if name == "tall" else []
            )
            return ActResult()
        if name == "tp" and arg.isdigit():
            self._require_host(uid)
            self._need_phase("lobby")
            self.themes_page = int(arg)
            return ActResult()
        if name == "rt":
            self._require_host(uid)
            self._need_phase("lobby")
            self.settings["random_theme"] = not self.settings["random_theme"]
            return ActResult()
        if name in ("start", "next"):
            self._require_host(uid)
            self._start_round()
            return ActResult()
        if name == "set":
            self._require_host(uid)
            self._need_phase("result")
            self.phase = "lobby"
            self.host_ui = "settings"
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
            self.guess_ui.setdefault(str(uid), {"page": 0, "word": None})
            return ActResult()
        if name in ("pg", "gp", "no", "cancel", "yes", "skip"):
            return self._guess_action(uid, name, arg)
        raise RoomError("Это действие сейчас недоступно")

    def _need_phase(self, phase: str) -> None:
        if self.phase != phase:
            raise RoomError("Это действие сейчас недоступно")

    def _need_playing_discussion(self) -> None:
        if self.phase != "playing" or self.round is None or self.round.phase != Phase.DISCUSSION:
            raise RoomError("Это действие сейчас недоступно")

    def _cycle(self, key: str) -> None:
        field_name, options = OPTIONS[key]
        if field_name == "max_players":
            options = [o for o in options if o >= len(self.players)] or [options[-1]]
        current = self.settings[field_name]
        index = options.index(current) if current in options else -1
        self.settings[field_name] = options[(index + 1) % len(options)]

    def _start_round(self) -> None:
        if self.phase not in ("lobby", "result"):
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
            )
        except ValueError as err:
            raise RoomError(str(err)) from err
        self.previous_spy = self.round.spy_id
        self.round_no += 1
        self.phase = "dealing"
        self.deadline = None
        self.guess_ui = {}
        self.host_ui = "lobby"
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
        if str(uid) == self.round.spy_id:
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
        self._note("Обсуждение началось")

    def current(self) -> str:
        return self.round.current()

    def _apply_turn_event(self, event: str, now: float) -> None:
        if self.round.phase == Phase.FINISHED:
            self._finish_round()
            return
        if event == "vote":
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

    def say(self, uid: int, text: str, now: float) -> None:
        player = self._member(uid)
        if self.phase != "playing" or self.round is None or self.round.phase != Phase.DISCUSSION:
            raise RoomError("Сейчас нельзя писать в чат")
        if str(uid) != self.round.current():
            raise RoomError("Писать можно только в свой ход")
        cleaned = " ".join(text.split())
        if not cleaned:
            raise RoomError("Пустое сообщение")
        if len(cleaned) > MAX_MESSAGE_LEN:
            raise RoomError(f"Слишком длинное сообщение (максимум {MAX_MESSAGE_LEN} символов)")
        self.chat.append([player["name"], cleaned])
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

    def _guess_words(self) -> list[str]:
        return sorted(self.round.allowed_words, key=str.casefold)

    def _guess_action(self, uid: int, name: str, arg: str) -> ActResult:
        self._need_phase("playing")
        me = str(uid)
        ui = self.guess_ui.get(me)
        if ui is None:
            raise RoomError("Панель угадывания закрыта")
        last_chance = self.round.phase == Phase.LAST_CHANCE
        words = self._guess_words()
        if name == "pg" and arg.isdigit():
            ui["page"] = int(arg)
            return ActResult()
        if name == "gp" and arg.isdigit() and int(arg) < len(words):
            ui["word"] = words[int(arg)]
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
            self._require_spy(uid)
            correct = self.round.guess(me, word)
            if self.round.phase == Phase.FINISHED:
                self._finish_round()
                return ActResult()
            return ActResult(alert="Верно!" if correct else "Неверно. Попытка использована.")
        raise RoomError("Это действие сейчас недоступно")

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
                "names": {str(p["id"]): p["name"] for p in self.players},
            }
        )
        self.phase = "result"
        self.deadline = None
        self.guess_ui = {}

    def _cancel_round(self) -> None:
        self.round = None
        self.phase = "lobby"
        self.round_no = max(0, self.round_no - 1)
        self.deadline = None
        self.guess_ui = {}
        self.host_ui = "lobby"
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
            self.deadline = None
            self._note("Круг завершён: большинство за голосование. Голосование началось")
        elif self.round.phase == Phase.DISCUSSION:
            self.deadline = now + self.settings["turn_seconds"]

    def _leave(self, uid: int, now: float) -> ActResult:
        player = self._member(uid)
        pid = str(uid)
        name = player["name"]
        active = self.phase in ("dealing", "playing") and self.round is not None
        penalized = active and not self.is_test
        self.guess_ui.pop(pid, None)
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
        return f"https://t.me/{bot_username}?start=join_{self.token}"

    def view_for(self, uid: int, bot_username: str = "", now: float | None = None) -> Screen:
        now = time.time() if now is None else now
        self._member(uid)
        if self.phase == "lobby":
            return self._lobby_view(uid, bot_username)
        if self.phase == "dealing":
            return self._dealing_view(uid)
        if self.phase == "playing":
            return self._playing_view(uid, now)
        if self.phase == "result":
            return self._result_view(uid)
        return Screen(
            f"<b>Игра завершена!</b>\n\nИтоговая таблица:\n{self.table()}",
            [[("В меню", "leave")]],
            html=True,
        )

    def _chat_text(self) -> str:
        lines = []
        for author, text in self.chat[-CHAT_KEEP:]:
            lines.append(f"• {esc(text)}" if author is None else f"<b>{esc(author)}:</b> {esc(text)}")
        return "\n".join(lines)

    def _lobby_view(self, uid: int, bot_username: str) -> Screen:
        host = uid == self.host_id
        if host and self.host_ui == "settings":
            return self._settings_view()
        if host and self.host_ui == "themes":
            return self._themes_view()
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
            + f"\n<b>⚙️ Настройки:</b> {self.settings['rounds']} раундов · {self.settings['turn_seconds']} с на ход"
        )
        text += self._themes_notice()
        if self.round_no:
            text += f"\n\n<b>🏆 Результаты после {self.round_no} раундов</b>\n{self.table()}"
        if self.chat:
            text += f"\n\n{self._chat_text()}"
        rows: list[list[tuple[str, str]]] = []
        if host:
            rows.append([("Начать игру", "start")])
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
            if key == "max" and value < len(self.players):
                continue
            label = f"[x] {value}" if value == current else str(value)
            buttons.append((label, f"sv:{key}:{value}"))
        return [[(title, "noop")], buttons]

    def _order_label(self) -> str:
        return "случайный" if self.settings["random_theme"] else "по очереди"

    def _order_suffix(self) -> str:
        if len(self.settings["themes"]) > 1:
            return f" (порядок: {self._order_label()})"
        return ""

    def _settings_view(self) -> Screen:
        rows: list[list[tuple[str, str]]] = []
        if not self.is_test:
            rows += self._option_rows("Игроков в комнате", "max")
        rows += self._option_rows("Раундов в игре", "rounds")
        rows += self._option_rows("Секунд на ход", "turn")
        rows += [
            [("Темы: выбрать", "ui:themes")],
            [(f"Порядок тем: {self._order_label()}", "rt")],
            [("Готово, назад в лобби", "ui:lobby")],
        ]
        return Screen(
            "<b>Настройки комнаты</b>\n\nНажмите на нужное значение, оно сохранится сразу.\n"
            f"Темы сейчас: {esc(self.themes_label())}{self._order_suffix()}" + self._themes_notice(),
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

    def _dealing_view(self, uid: int) -> Screen:
        seen = sum(1 for p in self.players if p["seen"])
        lines = "\n".join(
            f"{'[x]' if p['seen'] else '[ ]'} {esc(p['name'])}" for p in self.players
        )
        rows = [[("Показать роль", "role")]]
        if uid == self.host_id:
            rows.append([("Начать обсуждение", "force")])
        rows.append([("Выйти", "leave")])
        return Screen(
            f"<b>Раунд {self.round_no} из {self.settings['rounds']}</b>\n\n"
            "Нажмите «Показать роль»: она появится во всплывающем окне и не останется в чате."
            f"\n\nПосмотрели: {seen} из {len(self.players)}\n{lines}",
            rows,
            html=True,
        )

    def _playing_view(self, uid: int, now: float) -> Screen:
        rnd = self.round
        me = str(uid)
        head = (
            f"<b>Раунд {self.round_no} из {self.settings['rounds']}</b> · "
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
        listing = "\n".join(
            f"{'>' if i == idx else '-'} {i + 1}. {esc(self.name_of(p))}"
            for i, p in enumerate(order)
        )
        text = (
            f"{head}\n\n🔄 Круг {rnd.circle_no}\n\n"
            f"🎙 Сейчас говорит: <b>{esc(self.name_of(current))}</b>\n⏳ Осталось около {left} с\n"
            f"Далее: {esc(self.name_of(rnd.next_player()))}\n\n{listing}\n\n"
            "В свой ход каждый решает, нужно ли голосование. "
            "Оно начнётся после круга, если за него больше половины игроков."
        )
        if me == current:
            text += "\n\n<b>Ваш ход!</b> Напишите сообщение в чат бота."
        if self.chat:
            text += f"\n\n{self._chat_text()}"
        rows: list[list[tuple[str, str]]] = []
        if me == current:
            rows.append([("Закончить ход", "endturn:0")])
            rows.append([("Закончить ход и предложить голосование", "endturn:1")])
        elif me == str(self.host_id):
            rows.append([("Пропустить ход игрока", "endturn:0")])
        rows.append([("Угадать слово", "gu"), ("Моя роль", "role")])
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
        rows.append([("Угадать слово", "gu"), ("Моя роль", "role")])
        rows.append([("Выйти", "leave")])
        status = "Ваш голос принят, можно изменить до конца." if chosen else "Кого вы считаете шпионом?"
        return Screen(
            f"{head}\n\n<b>Тайное голосование</b>{note}\n"
            f"Проголосовали: {len(rnd.votes)} из {len(rnd.players)}\n\n{status}",
            rows,
            html=True,
        )

    def _left_seconds(self, now: float, limit: int) -> int:
        remaining = max(0.0, (self.deadline or now) - now)
        return min(limit, math.ceil(remaining / 10) * 10)

    def _guess_view(self, head: str, me: str, now: float | None = None) -> Screen:
        ui = self.guess_ui.get(me) or {"page": 0, "word": None}
        words = self._guess_words()
        last_chance = self.round.phase == Phase.LAST_CHANCE
        word = ui.get("word")
        if word:
            return Screen(
                f"{head}\n\nВы выбрали: <b>{esc(word)}</b>\n\nОтправить ответ? "
                + (
                    "Ошибка сейчас означает поражение."
                    if self.round.phase == Phase.DISCUSSION
                    else "Верный ответ сейчас даст ничью."
                ),
                [[("Да", "yes"), ("Другое слово", "no")]],
                html=True,
            )
        pages = max(1, -(-len(words) // PAGE_SIZE))
        page = min(ui.get("page", 0), pages - 1)
        chunk = words[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
        rows = []
        for i in range(0, len(chunk), 2):
            rows.append(
                [(chunk[j], f"gp:{page * PAGE_SIZE + j}") for j in range(i, min(i + 2, len(chunk)))]
            )
        nav = []
        if page > 0:
            nav.append(("Назад", f"pg:{page - 1}"))
        if page < pages - 1:
            nav.append(("Вперёд", f"pg:{page + 1}"))
        if nav:
            rows.append(nav)
        if last_chance:
            rows.append([("Не угадывать", "skip")])
        else:
            rows.append([("Отмена", "cancel")])
        left = (
            f" Осталось около {self._left_seconds(now, LAST_CHANCE_SECONDS)} с."
            if last_chance and now is not None
            else ""
        )
        if last_chance:
            title = f"Последний шанс! Выберите слово.{left}"
        elif self.round.phase == Phase.DISCUSSION:
            title = (
                "Выберите слово. Попытка одна: верный ответ даёт вам победу, "
                "ошибка означает поражение."
            )
        else:
            title = (
                "Выберите слово. Попытка одна: верный ответ во время голосования даёт ничью, "
                "ошибка сжигает попытку."
            )
        return Screen(f"{head}\n\n{title}\nСтраница {page + 1}/{pages}", rows, html=True)

    def _secret_guess_line(self) -> str:
        rnd = self.round
        if rnd is None or not rnd.secret_guess_word:
            return ""
        verdict = "верно" if rnd.secret_guess_correct else "неверно"
        return f"\n\nТайная попытка шпиона: {esc(rnd.secret_guess_word)} ({verdict})"

    def _result_view(self, uid: int) -> Screen:
        info = self.history[-1]
        lines = "\n".join(
            f"{esc(p['name'])}: +{info['deltas'].get(str(p['id']), 0)}" for p in self.players
        )
        text = (
            f"<b>Итоги раунда {info['round']}</b>\n\n"
            f"Шпион: <b>{esc(self.name_of(info['spy']))}</b>\n"
            f"Слово: <b>{esc(info['word'])}</b>\n"
            f"{REASONS[Outcome(info['outcome'])]}\n\n"
            f"Очки за раунд:\n{lines}\n\nТаблица:\n{self.table()}"
        )
        text += self._secret_guess_line()
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
            "used_words": list(self.used_words),
            "previous_spy": self.previous_spy,
            "history": list(self.history),
            "last_deltas": dict(self.last_deltas),
            "guess_ui": {k: dict(v) for k, v in self.guess_ui.items()},
            "host_ui": self.host_ui,
            "themes_page": self.themes_page,
            "themes_notice": self.themes_notice,
            "session_id": self.session_id,
        }

    @classmethod
    def from_dict(
        cls, data: dict, library: PackLibrary | None = None, rng: random.Random | None = None
    ) -> Room:
        room = cls(data["code"], data["token"], data["host_id"], data["title"], library, rng)
        for key, value in data.items():
            if key == "round":
                room.round = Round.from_dict(value) if value else None
            elif key not in ("code", "token", "host_id", "title", "turn_idx", "guess_ui"):
                setattr(room, key, value)
        stored = data.get("guess_ui")
        if isinstance(stored, dict) and "page" not in stored:
            room.guess_ui = {str(k): dict(v) for k, v in stored.items()}
        room.prune_missing_themes()
        return room
