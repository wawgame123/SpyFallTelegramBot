import random

from app.game.engine import Round
from app.game.packs import PackLibrary
from app.game.roles import build_turn_order, choose_spy, pick_word


def choose_theme(
    themes: list[str],
    random_order: bool,
    round_no: int,
    previous_theme: str | None,
    rng: random.Random,
) -> str:
    if len(themes) == 1:
        return themes[0]
    if random_order:
        pool = [t for t in themes if t != previous_theme] or list(themes)
        return rng.choice(pool)
    return themes[(max(round_no, 1) - 1) % len(themes)]


def create_round(
    library: PackLibrary,
    rng: random.Random,
    pids: list[str],
    themes: list[str],
    random_theme: bool,
    used_words: list[str],
    previous_spy: str | None,
    guesses: int = 1,
    spy_override: str | None = None,
    round_no: int = 1,
    previous_theme: str | None = None,
    open_guess: bool = False,
) -> tuple[Round, list[str]]:
    if not themes:
        raise ValueError("Не выбрано ни одной темы")
    theme_id = choose_theme(themes, random_theme, round_no, previous_theme, rng)
    if not library.has(theme_id):
        raise ValueError("Выбранная тема недоступна")
    pack = library.get(theme_id)
    words = list(pack.words)
    if not words:
        raise ValueError("В выбранной теме нет слов")
    word = pick_word(words, used_words, rng)
    spy = spy_override if spy_override in pids else choose_spy(pids, previous_spy, rng)
    order = build_turn_order(pids, spy, rng)
    rnd = Round(
        players=list(pids),
        spy_id=spy,
        word=word,
        theme=pack.title,
        allowed_words=words,
        order=order,
        guesses_left=guesses,
        theme_id=theme_id,
        open_guess=open_guess,
    )
    exhausted = set(words) <= set(used_words) | {word}
    used = [w for w in used_words if w not in set(words)] if exhausted else list(used_words)
    used.append(word)
    return rnd, used
