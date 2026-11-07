import random


def choose_spy(players: list[str], previous_spy: str | None, rng: random.Random) -> str:
    candidates = [p for p in players if p != previous_spy] or list(players)
    return rng.choice(candidates)


def build_turn_order(players: list[str], spy_id: str, rng: random.Random) -> list[str]:
    order = list(players)
    rng.shuffle(order)
    civilians = [p for p in players if p != spy_id]
    if civilians and order[0] == spy_id:
        first = rng.choice(civilians)
        order.remove(first)
        order.insert(0, first)
    return order


def pick_word(words: list[str], used: list[str], rng: random.Random) -> str:
    fresh = [w for w in words if w not in used]
    return rng.choice(fresh or list(words))
