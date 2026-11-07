from __future__ import annotations

from dataclasses import dataclass



@dataclass(frozen=True)
class Pack:
    id: str
    title: str
    description: str
    category: str
    words: tuple[str, ...]


class PackLibrary:
    def __init__(self, packs: list[Pack]):
        self._packs = {pack.id: pack for pack in packs}
        self._order = [pack.id for pack in packs]

    def with_packs(self, extra: list[Pack]) -> PackLibrary:
        merged = dict(self._packs)
        order = list(self._order)
        for pack in extra:
            if pack.id not in merged:
                order.append(pack.id)
            merged[pack.id] = pack
        return PackLibrary([merged[i] for i in order])

    def packs(self) -> list[Pack]:
        return [self._packs[pack_id] for pack_id in self._order]

    def get(self, pack_id: str) -> Pack:
        return self._packs[pack_id]

    def has(self, pack_id: str) -> bool:
        return pack_id in self._packs

    def words(self, pack_ids: list[str]) -> list[str]:
        seen: dict[str, None] = {}
        for pack_id in pack_ids:
            for word in self._packs[pack_id].words:
                seen.setdefault(word, None)
        return list(seen)


def load_default_library() -> PackLibrary:
    """Return an empty library; production code must inject the MySQL library."""
    return PackLibrary([])
