import asyncio
import hashlib
import json
import logging
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import aiohttp
from aiohttp import web

from app.models.database.worker import run_db
from app.views.telegram.ui import delete_quietly
from app.models.game.room import CHAT_KEEP, CUSTOM_RULE_DIGITS, MAX_MESSAGE_LEN, MIN_PLAYERS, MODES, RoomError
from app.models.services.pack_service import PackChanged, PackError
from app.models.services.penalty import format_duration
from app.models.services.room_access import RoomPasswordRequired, hash_room_password
from app.controllers.web.auth import LOOPBACK_HOSTS, TelegramAuth, validate_mini_app
from app.controllers.web.avatars import TelegramAvatars
from app.controllers.web.catalog import Catalog
from app.controllers.web.cosmetics import Cosmetics
from app.controllers.web.friends import Friends

ASSETS = Path(__file__).resolve().parents[2] / "views" / "web" / "dist"
log = logging.getLogger(__name__)


def web_origin():
    origin = os.getenv("WEB_PUBLIC_URL", "").strip().rstrip("/")
    if not origin:
        return None
    parsed = urlsplit(origin)
    if not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise ValueError("WEB_PUBLIC_URL должен содержать только адрес сайта")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS
    ):
        raise ValueError("WEB_PUBLIC_URL должен использовать HTTPS")
    return origin


class RoomEvents:
    def __init__(self):
        self.listeners = {}
        self.total = 0

    def changed(self, room_id):
        for _, event in self.listeners.get(room_id, ()):
            event.set()


class WebGame:
    def __init__(
        self, runtime, public_url, bot_token, client_id="", client_secret="", *, packs=None, stats=None, factory=None,
    ):
        self.runtime = runtime
        self.rooms = runtime.service
        self.auth = TelegramAuth(public_url, bot_token, client_id, client_secret, factory=factory)
        self.avatars = TelegramAvatars(runtime.bot)
        self.cosmetics = Cosmetics(factory)
        self.friends = Friends(factory, self.auth, self.avatars, self.cosmetics) if factory is not None else None
        self.catalog = Catalog(self.auth, packs, stats, self.avatars, self.cosmetics, self.friends) if packs is not None and stats is not None else None
        self.events = RoomEvents()
        self.key = secrets.token_bytes(32)
        self.lobbies = []
        self.lobbies_until = 0.0
        self.lobby_lock = asyncio.Lock()
        self.password_slots = asyncio.Semaphore(2)
        self.create_times = {}

    async def index(self, request):
        index = ASSETS / "index.html"
        if not index.is_file():
            raise web.HTTPServiceUnavailable(text="Сначала соберите сайт: npm --prefix app/views/web run build")
        return web.FileResponse(index, headers={
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self' https://telegram.org; img-src 'self' data:; "
                "object-src 'none'; base-uri 'none'; form-action 'self'; "
                "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org"
            ),
        })

    async def login(self, request):
        return self.auth.start_login(request)

    async def callback(self, request):
        return await self.auth.finish_login(request, self.http)

    async def local_login(self, request):
        data = await self.body(request)
        response, entry = await self.auth.local_login(request, data.get("code"))
        messages = [entry.command_id]
        if entry.reply_id is not None:
            messages.append(entry.reply_id)
        try:
            async with asyncio.timeout(4):
                await asyncio.gather(
                    *(delete_quietly(self.runtime.bot, entry.chat_id, message) for message in messages)
                )
        except TimeoutError:
            log.warning("Не удалось вовремя удалить сообщения локального входа")
        return response

    async def mini_login(self, request):
        data = await self.body(request)
        try:
            player = validate_mini_app(data.get("initData"), self.auth.bot_token)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise web.HTTPUnauthorized(text="Откройте Mini App заново из Telegram") from None
        return web.json_response({"session": self.auth.issue(player)})

    async def logout(self, request):
        await self.auth.revoke(request)
        response = web.json_response({})
        response.del_cookie(self.auth.session_cookie)
        return response

    async def me(self, request):
        session = self.auth.optional_session(request)
        player = session[2] if session else None
        equipped = await self.cosmetics.equipped([player.id]) if player else {}
        return web.json_response({
            "player": {
                "name": player.name, "avatar": self.avatars.link(player.id, "profile"),
                "cosmetics": equipped.get(player.id),
            } if player else None,
            "login": self.auth.login_enabled, "local_login": self.auth.local_enabled,
            "bot": self.runtime.bot_username, "friends": self.friends is not None,
        })

    async def body(self, request):
        try:
            value = await request.json()
        except (ValueError, UnicodeError):
            raise web.HTTPBadRequest(text="Некорректные данные") from None
        if not isinstance(value, dict):
            raise web.HTTPBadRequest(text="Некорректные данные")
        return value

    async def equip_cosmetics(self, request):
        _, _, player = self.auth.session(request)
        look = await self.cosmetics.save(player.id, await self.body(request))
        room_id = await run_db(self.rooms.room_id_of, player.id)
        if room_id is not None:
            self.events.changed(room_id)
        return web.json_response({"cosmetics": look})

    async def directory(self, request):
        async with self.lobby_lock:
            if time.monotonic() >= self.lobbies_until:
                self.lobbies = await run_db(self.rooms.list_lobbies)
                self.lobbies_until = time.monotonic() + 3
        viewer_id = self.auth.viewer_id(request)
        social = self.friends if viewer_id is not None else None
        friend_ids = await social.ids(viewer_id) if social else ()
        ranked = [(lobby, [name for uid, name in lobby["members"] if uid in friend_ids])
                  for lobby in self.lobbies]
        ranked.sort(key=lambda pair: not pair[1])
        ranked = ranked[:40]
        equipped = await self.cosmetics.equipped(uid for lobby, _ in ranked for uid, _ in lobby["members"])
        lobbies = []
        for lobby, present in ranked:
            members = lobby["members"]
            card = {key: value for key, value in lobby.items() if key != "members"}
            card["avatars"] = [
                {"initial": name.strip()[:1].upper() or "?", "cosmetics": equipped.get(uid),
                 **(social.player(uid, name, own=uid == viewer_id) if social
                    else {"image": self.avatars.link(uid, "lobby")})}
                for uid, name in members
            ]
            card["friends"] = present
            lobbies.append(card)
        return web.json_response({"lobbies": lobbies})

    def chat_activity(self, room, equipped):
        return [
            [author, text, self.avatars.link(sender[0], "lobby"), equipped.get(sender[0])]
            if sender else [author, text, None, None]
            for author, text, *sender in room.chat[-CHAT_KEEP:]
        ]

    def lobby_board(self, room, viewer_id, equipped):
        settings = room.settings
        participants = []
        for member in room.players:
            uid, name = member["id"], member["name"]
            profile = self.friends.player(uid, name, own=uid == viewer_id) if self.friends else {
                "name": name, "image": self.avatars.link(uid, "lobby"),
            }
            participants.append({
                **profile, "host": uid == room.host_id, "ready": member["ready"],
                "score": room.scores.get(str(uid), 0), "cosmetics": equipped.get(uid),
            })
        return {
            "players": participants, "capacity": settings["max_players"],
            "rounds": settings["rounds"], "played": room.round_no, "turn": settings["turn_seconds"],
            "mode": MODES[settings.get("mode", "classic")], "themes": room.themes_label(),
            "random": settings["random_theme"], "themes_notice": room.themes_notice,
            "activity": self.chat_activity(room, equipped), "test_role": room._role_label() if room.is_test else None,
        }

    def discussion_board(self, room, viewer_id, equipped):
        rnd = room.round
        if room.phase != "playing" or rnd is None or rnd.phase.value != "discussion":
            return None
        if str(viewer_id) in room.guess_ui:
            return None
        current, following = rnd.current(), rnd.next_player()
        return {
            "round": room.round_no, "rounds": room.settings["rounds"], "practice": room.practice,
            "circle": rnd.circle_no, "theme": rnd.theme,
            "speaker": room.name_of(current), "next": room.name_of(following) if following != current else None,
            "order": [{"name": room.name_of(pid), "speaking": pid == current} for pid in rnd.order],
            "activity": self.chat_activity(room, equipped), "hidden": rnd.hidden,
        }

    async def room_view(self, request):
        _, _, player = self.auth.session(request)
        snapshot = await run_db(self.rooms.web_room, player.id, self.key)
        if snapshot is None:
            return web.json_response({"room": None})
        rid, room, revision = snapshot
        screen = room.view_for(player.id)
        commands = {action for row in screen.rows for _, action in row}
        is_lobby = room.phase == "lobby" and "ui:lobby" not in commands and "tall" not in commands
        avatar_ids = {entry[2] for entry in room.chat[-CHAT_KEEP:] if len(entry) == 3}
        if is_lobby:
            avatar_ids.update(member["id"] for member in room.players)
        equipped = await self.cosmetics.equipped(avatar_ids)
        appearance_revision = hashlib.sha256(json.dumps(equipped, sort_keys=True).encode()).hexdigest()
        discussion = self.discussion_board(room, player.id, equipped)
        settings = None
        if "mode:classic" in commands:
            settings = {
                "rounds": {"value": str(room.settings["rounds"]), "min": max(1, room.round_no)},
                "digits": CUSTOM_RULE_DIGITS,
            }
            if not room.is_test:
                settings["max"] = {
                    "value": str(room.settings["max_players"]), "min": max(MIN_PLAYERS, len(room.players)),
                }
        return web.json_response({"room": {
            "id": rid, "revision": revision, "appearance_revision": appearance_revision,
            "title": room.title, "code": room.code,
            "phase": room.round.phase.value if room.phase == "playing" and room.round else room.phase,
            "text": screen.text, "buttons": screen.rows, "deadline": room.deadline,
            "lobby": self.lobby_board(room, player.id, equipped) if is_lobby else None,
            "discussion": discussion, "guess": room.guess_sheet(player.id),
            "settings": settings,
            "theme_count": len(room.settings["themes"]) if "tall" in commands else None,
            "server_time": time.time(),
            "can_chat": is_lobby or bool(discussion and room.round.current() == str(player.id)),
            "share": self.auth.public_url + "/?" + urlencode({"join": room.token}),
            "telegram": room.link(self.runtime.bot_username) if self.runtime.bot_username else None,
        }})

    def publish(self, update):
        self.events.changed(update.room_id)
        if update.room is not None:
            self.runtime.schedule_refresh(update.room_id)

    async def create(self, request):
        _, _, player = self.auth.session(request)
        data = await self.body(request)
        now = time.monotonic()
        last = self.create_times.get(player.id, 0)
        if now - last < 2:
            raise web.HTTPTooManyRequests(text="Повторите через пару секунд")
        if len(self.create_times) >= 4096:
            self.create_times.pop(next(iter(self.create_times)))
        self.create_times[player.id] = now
        password = data.get("password", "")
        async with self.password_slots:
            password_hash = await asyncio.to_thread(hash_room_password, password)
        update = await run_db(
            self.rooms.create, player.id, player.name, via_web=True,
            listed=data.get("listed") is True, password_hash=password_hash,
        )
        self.publish(update)
        return web.json_response({"joined": True})

    async def join(self, request):
        _, _, player = self.auth.session(request)
        data = await self.body(request)
        join = self.rooms.join_token if "invite" in data else self.rooms.join_code
        entry = data.get("invite", data.get("code"))
        password = data.get("password")
        if not isinstance(entry, str) or len(entry) > 32 or (
            password is not None and (not isinstance(password, str) or len(password) > 64)
        ):
            raise web.HTTPBadRequest(text="Проверьте код и пароль лобби")
        update = await run_db(join, player.id, player.name, entry, via_web=True, password=password)
        self.publish(update)
        return web.json_response({"joined": True})

    async def action(self, request):
        _, _, player = self.auth.session(request)
        data = await self.body(request)
        rid, action, revision = data.get("room"), data.get("action"), data.get("revision")
        if type(rid) is not int or not 0 < rid < 2**31 or not isinstance(action, str) or len(action) > 64:
            raise web.HTTPBadRequest(text="Некорректное действие")
        if not isinstance(revision, str) or len(revision) != 64 or not revision.isascii():
            raise web.HTTPBadRequest(text="Обновите экран комнаты")
        async with self.runtime.lock(rid):
            if action == "leave":
                prompt = await run_db(self.rooms.leave_confirmation, player.id, expected_room_id=rid)
                return web.json_response({"confirm": prompt})
            confirmation = data.get("confirmation")
            if action == "leave_confirm" and (not isinstance(confirmation, str) or len(confirmation) > 512):
                raise web.HTTPBadRequest(text="Сначала подтвердите выход")
            panel = await run_db(self.rooms.panel_id_of, player.id) if action == "leave_confirm" else None
            update = await run_db(
                self.rooms.act, player.id, action, expected_room_id=rid,
                web_revision=(self.key, revision),
                expected_leave_text=confirmation if action == "leave_confirm" else None,
            )
        if update.changed or update.left_user:
            self.publish(update)
        if update.left_user and panel:
            await delete_quietly(self.runtime.bot, player.id, panel)
        alert = update.alert
        if update.penalty:
            alert = f"Вы покинули игру. Блокировка онлайн-игр: {format_duration(update.penalty[1] * 60)}."
        return web.json_response({"alert": alert})

    async def chat(self, request):
        _, _, player = self.auth.session(request)
        data = await self.body(request)
        rid, text, phase = data.get("room"), data.get("text"), data.get("phase")
        if type(rid) is not int or not 0 < rid < 2**31 or not isinstance(text, str) or len(text) > MAX_MESSAGE_LEN:
            raise web.HTTPBadRequest(text=f"Введите сообщение до {MAX_MESSAGE_LEN} символов")
        if phase not in (None, "lobby", "discussion"):
            raise web.HTTPBadRequest(text="Неизвестный чат")
        if not self.runtime.allow_chat_message(player.id):
            raise web.HTTPTooManyRequests(text="Повторите через секунду")
        async with self.runtime.lock(rid):
            update = await run_db(self.rooms.say, player.id, text, expected_room_id=rid, expected_phase=phase)
        self.publish(update)
        return web.json_response({})

    async def stream(self, request):
        key, until, player = self.auth.session(request)
        rid = await run_db(self.rooms.room_id_of, player.id)
        if rid is None:
            raise web.HTTPNotFound(text="Комната закрыта")
        listeners = self.events.listeners.setdefault(rid, set())
        if self.events.total >= 512 or sum(uid == player.id for uid, _ in listeners) >= 3:
            if not listeners:
                self.events.listeners.pop(rid, None)
            raise web.HTTPTooManyRequests(text="Слишком много подключений")
        signal = asyncio.Event()
        entry = (player.id, signal)
        listeners.add(entry)
        self.events.total += 1
        response = web.StreamResponse(headers={
            "Content-Type": "text/event-stream", "Cache-Control": "no-store", "X-Accel-Buffering": "no",
        })
        try:
            await response.prepare(request)
            await response.write(b"data: change\n\n")
            while time.monotonic() < until and key in self.auth.sessions:
                try:
                    await asyncio.wait_for(signal.wait(), timeout=15)
                except TimeoutError:
                    await response.write(b": heartbeat\n\n")
                else:
                    signal.clear()
                    await response.write(b"data: change\n\n")
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            listeners.discard(entry)
            self.events.total -= 1
            if not listeners:
                self.events.listeners.pop(rid, None)
        return response

    async def lifecycle(self, app):
        await self.auth.restore_sessions()
        self.runtime.on_room_change = self.events.changed
        if self.auth.local_enabled:
            self.runtime.local_web_auth = self.auth
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as self.http:
                yield
        finally:
            self.runtime.on_room_change = None
            self.runtime.local_web_auth = None

    def application(self):
        @web.middleware
        async def access(request, handler):
            if request.method == "POST" and request.headers.get("Origin") != self.auth.public_url:
                response = web.json_response({"error": "Откройте официальный адрес сайта"}, status=403)
            else:
                try:
                    response = await handler(request)
                except RoomPasswordRequired as error:
                    response = web.json_response({"error": str(error), "password": True}, status=409)
                except RoomError as error:
                    response = web.json_response({"error": str(error)}, status=409)
                except PackChanged as error:
                    response = web.json_response({"error": str(error)}, status=409)
                except PackError as error:
                    response = web.json_response({"error": str(error)}, status=422)
                except web.HTTPException as error:
                    if request.path.startswith("/api/"):
                        response = web.json_response({"error": error.text}, status=error.status)
                    else:
                        raise
            if not response.prepared:
                response.headers.setdefault("X-Content-Type-Options", "nosniff")
                response.headers.setdefault("Referrer-Policy", "no-referrer")
                if not request.path.startswith("/static/"):
                    response.headers.setdefault("Cache-Control", "no-store")
            return response

        app = web.Application(middlewares=[access], client_max_size=16384)
        app.cleanup_ctx.append(self.lifecycle)
        app.add_routes([
            web.get("/", self.index), web.get("/auth/login", self.login),
            web.get("/avatar/{reference:[0-9a-f]{32}}", self.avatars.serve),
            web.get("/auth/callback", self.callback), web.post("/api/auth/mini", self.mini_login),
            web.post("/api/auth/local", self.local_login),
            web.post("/api/logout", self.logout), web.get("/api/me", self.me),
            web.get("/api/cosmetics", self.cosmetics.catalog), web.post("/api/cosmetics", self.equip_cosmetics),
            web.get("/api/lobbies", self.directory), web.get("/api/room", self.room_view),
            web.post("/api/lobbies", self.create), web.post("/api/join", self.join),
            web.post("/api/room/action", self.action), web.post("/api/room/chat", self.chat),
            web.get("/api/room/events", self.stream),
        ])
        if ASSETS.is_dir():
            app.add_routes([web.static("/static/", ASSETS)])
        if self.catalog is not None:
            self.catalog.add_routes(app)
        if self.friends is not None:
            app.add_routes([
                web.get("/api/friends", self.friends.list),
                web.get("/api/friends/player", self.friends.preview),
                web.post("/api/friends", self.friends.change),
            ])
        return app


async def start_web(runtime, url, token, *, packs, stats, factory):
    game = WebGame(runtime, url, token, os.getenv("TELEGRAM_LOGIN_CLIENT_ID", ""),
                   os.getenv("TELEGRAM_LOGIN_CLIENT_SECRET", ""), packs=packs, stats=stats, factory=factory)
    host = os.getenv("WEB_HOST", "127.0.0.1")
    if game.auth.local_enabled and host not in LOOPBACK_HOSTS:
        raise ValueError("Для локального входа WEB_HOST должен быть адресом этого ПК")
    runner = web.AppRunner(game.application(), access_log=None, shutdown_timeout=3)
    await runner.setup()
    try:
        port = int(os.getenv("WEB_PORT", "8080"))
        await web.TCPSite(runner, host, port).start()
    except BaseException:
        await runner.cleanup()
        raise
    log.info("Веб-интерфейс Spyfall запущен на порту %s", port)
    return runner
