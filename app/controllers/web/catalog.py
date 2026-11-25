from aiohttp import web
from sqlalchemy.exc import IntegrityError

from app.models.database.worker import run_db
from app.models.game.packs import word_key
from app.models.services.pack_service import (
    MAX_TITLE_LEN, MAX_WORD_LEN, MAX_WORDS, SCOPES, PackError, split_words,
)
from app.models.services.stats_service import PERIOD_DAYS, SORTS

PAGE_SIZE = 12
PACK_BODY_LIMIT = 128 * 1024


def pack_card(info, viewer_id):
    return {
        "id": info.id, "title": info.title, "category": info.category,
        "owner": info.owner_name, "words_count": info.words_count,
        "public": info.is_public, "hidden": info.is_hidden,
        "editable": viewer_id is not None and info.owner_id == viewer_id,
        "favorite": info.is_favorite,
    }


def rating_row(row, viewer_id, avatars, equipped, friends=None):
    return {
        "name": row.name, "place": row.place, "games": row.games,
        "points": row.points, "wins": row.wins, "spy": row.spy_wins,
        "rate": row.win_rate, "mine": row.user_id == viewer_id,
        "image": avatars.link(row.user_id, "lobby"), "cosmetics": equipped.get(row.user_id),
        "friend_ref": friends.reference(row.user_id, row.name)
        if friends and viewer_id is not None and row.user_id != viewer_id and row.user_id > 0 else None,
    }


async def pack_fields(request):
    try:
        data = await request.clone(client_max_size=PACK_BODY_LIMIT).json()
    except (ValueError, UnicodeError):
        raise web.HTTPBadRequest(text="Некорректные данные набора") from None
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="Некорректные данные набора")
    title, raw_words, public = data.get("title"), data.get("words"), data.get("public")
    if not isinstance(title, str) or len(title) > MAX_TITLE_LEN:
        raise PackError(f"Введите название до {MAX_TITLE_LEN} символов")
    if not isinstance(raw_words, str) or not isinstance(public, bool):
        raise web.HTTPBadRequest(text="Некорректные данные набора")
    words = split_words(raw_words)
    if len(words) > MAX_WORDS:
        raise PackError(f"В наборе может быть не более {MAX_WORDS} слов")
    if any(len(word) > MAX_WORD_LEN for word in words):
        raise PackError(f"Одно слово может содержать до {MAX_WORD_LEN} символов")
    if len(set(map(word_key, words))) != len(words):
        raise PackError("Уберите повторяющиеся слова")
    return title, words, public, data.get("revision")


class Catalog:
    def __init__(self, auth, packs, stats, avatars, cosmetics, friends=None):
        self.auth = auth
        self.packs = packs
        self.stats = stats
        self.avatars = avatars
        self.cosmetics = cosmetics
        self.friends = friends

    async def profile(self, request):
        _, _, player = self.auth.session(request)
        profile = await run_db(self.stats.profile, player.id)
        equipped = await self.cosmetics.equipped([player.id])
        stats = {
            "games": profile.games, "wins": profile.wins, "win_rate": profile.win_rate,
            "civilian_wins": profile.civilian_wins, "spy_wins": profile.spy_wins,
            "losses": profile.losses, "draws": profile.draws, "points": profile.points,
            "lobbies": profile.lobbies,
        } if profile else None
        return web.json_response({
            "name": player.name, "stats": stats, "avatar": self.avatars.link(player.id, "profile"),
            "cosmetics": equipped.get(player.id),
        })

    async def ranking(self, request):
        viewer_id = self.auth.viewer_id(request)
        sort = request.query.get("sort", "points")
        period = request.query.get("period", "all")
        if sort not in SORTS or period not in PERIOD_DAYS:
            raise web.HTTPBadRequest(text="Неизвестный фильтр рейтинга")
        rows, own_rank = await run_db(self.stats.rating, sort, period, viewer_id, limit=20)
        ids = [row.user_id for row in rows]
        if own_rank:
            ids.append(own_rank.user_id)
        equipped = await self.cosmetics.equipped(ids)
        return web.json_response({
            "rows": [rating_row(row, viewer_id, self.avatars, equipped, self.friends) for row in rows],
            "own_rank": rating_row(own_rank, viewer_id, self.avatars, equipped) if own_rank else None,
        })

    async def list_packs(self, request):
        scope = request.query.get("scope", "all")
        viewer_id = self.auth.session(request)[2].id if scope in ("my", "fav") else self.auth.viewer_id(request)
        page = request.query.get("page", "0")
        query = request.query.get("q", "").strip()
        if scope not in (*SCOPES, "all") or not (page.isascii() and page.isdigit() and len(page) <= 5):
            raise web.HTTPBadRequest(text="Неизвестный раздел или страница")
        if len(query) > MAX_TITLE_LEN:
            raise web.HTTPBadRequest(text=f"Поиск ограничен {MAX_TITLE_LEN} символами")
        page = int(page)
        rows, total = await run_db(self.packs.list_packs, viewer_id, scope, page, PAGE_SIZE, query)
        return web.json_response({
            "packs": [pack_card(row, viewer_id) for row in rows], "total": total, "size": PAGE_SIZE,
        })

    async def pack(self, request):
        viewer_id = self.auth.viewer_id(request)
        try:
            info, words, revision = await run_db(self.packs.details, viewer_id, pack_id(request))
        except PackError as error:
            raise web.HTTPNotFound(text=str(error)) from None
        result = {**pack_card(info, viewer_id), "words": words, "description": info.description}
        if result["editable"]:
            result["revision"] = revision
        return web.json_response(result)

    async def create_pack(self, request):
        _, _, player = self.auth.session(request)
        title, words, public, _ = await pack_fields(request)
        try:
            created = await run_db(
                self.packs.create_pack, player.id, player.name, title, words, public=public,
            )
        except IntegrityError:
            raise PackError("Не удалось сохранить набор. Проверьте, нет ли одинаковых слов.") from None
        return web.json_response({"id": created}, status=201)

    async def update_pack(self, request):
        _, _, player = self.auth.session(request)
        target = pack_id(request)
        title, words, public, revision = await pack_fields(request)
        if not isinstance(revision, str) or len(revision) != 64:
            raise web.HTTPBadRequest(text="Откройте карточку набора заново")
        try:
            await run_db(self.packs.update_pack, player.id, target, title, words, public, revision)
        except IntegrityError:
            raise PackError("Не удалось сохранить набор. Проверьте, нет ли одинаковых слов.") from None
        return web.json_response({"id": target})

    async def toggle_favorite(self, request):
        _, _, player = self.auth.session(request)
        favorite = await run_db(self.packs.toggle_favorite, player.id, pack_id(request))
        return web.json_response({"favorite": favorite})

    def add_routes(self, app):
        item = r"/api/packs/{pack_id:[0-9]{1,10}}"
        app.add_routes([
            web.get("/api/ranking", self.ranking), web.get("/api/profile", self.profile),
            web.get("/api/packs", self.list_packs), web.post("/api/packs", self.create_pack),
            web.get(item, self.pack), web.post(item, self.update_pack),
            web.post(item + "/favorite", self.toggle_favorite),
        ])


def pack_id(request):
    value = int(request.match_info["pack_id"])
    if not 0 < value <= 2147483647:
        raise web.HTTPNotFound(text="Набор не найден")
    return value
