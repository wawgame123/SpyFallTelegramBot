from collections import Counter


def majority_reached(yes_votes: int, active_players: int) -> bool:
    return yes_votes * 2 > active_players


def votes_needed(active_players: int) -> int:
    return active_players // 2 + 1


def tally(votes: dict[str, str]) -> tuple[dict[str, int], list[str]]:
    if not votes:
        return {}, []
    counts = Counter(votes.values())
    top = max(counts.values())
    leaders = sorted(player for player, number in counts.items() if number == top)
    return dict(counts), leaders
