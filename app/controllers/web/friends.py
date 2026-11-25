import asyncio
import json
from base64 import urlsafe_b64encode
from functools import lru_cache
from hashlib import sha256

from aiohttp import web
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.exc import IntegrityError

from app.models.database.friend_repository import FriendListFull, FriendRepository
from app.models.database.worker import run_db


class Friends:
    def __init__(self, factory, auth, avatars, cosmetics):
        self.repository = FriendRepository(factory)
        self.auth = auth
        self.avatars = avatars
        self.cosmetics = cosmetics
        secret = sha256(b"spyfall-web-friend-v1\0" + auth.bot_token.encode()).digest()
        self.cipher = Fernet(urlsafe_b64encode(secret))
        self.reference = lru_cache(maxsize=2048)(self._reference)
        self.lock = asyncio.Lock()

    def _reference(self, user_id, name):
        return self.cipher.encrypt(json.dumps([user_id, name[:80]], ensure_ascii=False).encode()).decode()

    def player(self, user_id, name, *, own=False):
        return {
            "name": name, "image": self.avatars.link(user_id, "lobby"),
            "friend_ref": self.reference(user_id, name) if user_id > 0 and not own else None,
        }

    async def list(self, request):
        _, _, viewer = self.auth.session(request)
        friends = await run_db(self.repository.list, viewer.id)
        equipped = await self.cosmetics.equipped(friends)
        return web.json_response({
            "invite": self.reference(viewer.id, viewer.name),
            "friends": [{**self.player(uid, name), "cosmetics": equipped.get(uid)} for uid, name in
                        sorted(friends.items(), key=lambda pair: pair[1].casefold())],
        })

    def decode(self, reference):
        if not isinstance(reference, str) or not 1 <= len(reference) <= 640:
            raise web.HTTPBadRequest(text="Ссылка на игрока недействительна")
        try:
            return json.loads(self.cipher.decrypt(reference.encode()))
        except (InvalidToken, ValueError):
            raise web.HTTPBadRequest(text="Ссылка на игрока недействительна") from None

    async def preview(self, request):
        _, _, viewer = self.auth.session(request)
        friend_id, name = self.decode(request.query.get("ref"))
        if friend_id == viewer.id:
            raise web.HTTPBadRequest(text="Это ваша собственная ссылка")
        friends = await run_db(self.repository.list, viewer.id)
        equipped = await self.cosmetics.equipped([friend_id])
        return web.json_response({
            **self.player(friend_id, name), "added": friend_id in friends, "cosmetics": equipped.get(friend_id),
        })

    async def change(self, request):
        _, _, viewer = self.auth.session(request)
        try:
            data = await request.json()
        except (ValueError, UnicodeError):
            raise web.HTTPBadRequest(text="Некорректные данные") from None
        if not isinstance(data, dict) or type(data.get("add")) is not bool:
            raise web.HTTPBadRequest(text="Некорректные данные")
        friend_id, name = self.decode(data.get("ref"))
        if friend_id == viewer.id:
            raise web.HTTPBadRequest(text="Нельзя добавить себя в друзья")
        async with self.lock:
            try:
                await run_db(self.repository.save, viewer.id, friend_id, name, data["add"])
            except FriendListFull:
                raise web.HTTPConflict(text="Список друзей заполнен") from None
            except IntegrityError:
                if friend_id not in await run_db(self.repository.list, viewer.id):
                    raise
        return web.json_response({"added": data["add"]})

    async def ids(self, viewer_id):
        return (await run_db(self.repository.list, viewer_id)).keys()
