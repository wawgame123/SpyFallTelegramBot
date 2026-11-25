import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit

import aiohttp
from aiohttp import web

from app.models.database.web_session_repository import WebSessionRepository
from app.models.database.worker import run_db

ISSUER = "https://oauth.telegram.org"
COOKIE = "spyfall_session"
LOGIN_COOKIE = "spyfall_login"
SESSION_SECONDS = 43200
REMEMBER_SECONDS = 30 * 24 * 60 * 60
SESSION_CAPACITY = 4096
LOGIN_SECONDS = 600
LOCAL_CODE_SECONDS = 180
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True)
class Player:
    id: int
    name: str


@dataclass
class LocalCode:
    expires: float
    player: Player
    chat_id: int
    command_id: int
    reply_id: int | None = None
    redeemed: bool = False


def player_from_claims(claims: dict) -> Player:
    identifier = claims.get("id")
    if type(identifier) is not int or not 0 < identifier < 2**63:
        raise ValueError("Telegram ID")
    name = claims.get("first_name") or claims.get("name") or claims.get("preferred_username")
    return Player(identifier, name[:80] if isinstance(name, str) else "Игрок")


def validate_mini_app(raw: str, bot_token: str, now: float | None = None) -> Player:
    if not isinstance(raw, str) or len(raw) > 8192:
        raise ValueError("initData")
    pairs = parse_qsl(raw, strict_parsing=True, keep_blank_values=True, max_num_fields=30)
    data = dict(pairs)
    if len(data) != len(pairs):
        raise ValueError("initData")
    proof = data.pop("hash", "")
    signed = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    key = hmac.digest(b"WebAppData", bot_token.encode(), "sha256")
    calculated = hmac.digest(key, signed.encode(), "sha256").hex()
    if not hmac.compare_digest(calculated, proof):
        raise ValueError("Signature")
    now = time.time() if now is None else now
    if not -30 <= now - int(data["auth_date"]) <= LOGIN_SECONDS:
        raise ValueError("Expired")
    return player_from_claims(json.loads(data["user"]))


class TelegramAuth:
    def __init__(self, public_url, bot_token, client_id="", client_secret="", *, factory=None):
        self.public_url = public_url
        self.bot_token = bot_token
        self.client_id = client_id
        self.client_secret = client_secret
        port = urlsplit(public_url).port if self.local_enabled else None
        self.session_cookie = f"{COOKIE}_{port}" if port else COOKIE
        self.login_flows = OrderedDict()
        self.local_codes = OrderedDict()
        self.sessions = OrderedDict()
        self._session_scope = hmac.digest(bot_token.encode(), b"spyfall-web:" + public_url.encode(), "sha256")
        self._remembered = WebSessionRepository(factory, self._session_scope.hex()) if factory is not None else None
        self._keys = {}
        self._keys_expire = 0.0

    @property
    def login_enabled(self):
        return bool(self.client_id and self.client_secret)

    @property
    def local_enabled(self):
        url = urlsplit(self.public_url)
        return url.scheme == "http" and url.hostname in LOOPBACK_HOSTS

    def local_code(self, player: Player, chat_id: int, command_id: int) -> tuple[str, LocalCode]:
        code = secrets.token_hex(8).upper()
        pending = LocalCode(time.monotonic() + LOCAL_CODE_SECONDS, player, chat_id, command_id)
        self.local_codes[code] = pending
        if len(self.local_codes) > 512:
            self.local_codes.popitem(last=False)
        return code, pending

    async def local_login(self, request, code):
        if not self.local_enabled or request.remote not in LOOPBACK_HOSTS:
            raise web.HTTPNotFound(text="Локальный вход недоступен")
        entry = self.local_codes.pop(code.strip().upper(), None) if isinstance(code, str) and len(code) <= 32 else None
        if entry is None or entry.expires <= time.monotonic():
            raise web.HTTPUnauthorized(text="Код неверный или уже истёк. Получите новый через /web.")
        response = web.json_response({})
        try:
            await self.remember(response, entry.player)
        except Exception:
            if entry.expires > time.monotonic():
                self.local_codes[code.strip().upper()] = entry
            raise
        entry.redeemed = True
        return response, entry

    def _session_digest(self, token):
        if not token or len(token) != 43 or not token.isascii():
            return None
        return hmac.digest(self._session_scope, token.encode(), "sha256").hex()

    def _cache_session(self, digest, expires, player):
        self.sessions[digest] = (expires, player)
        if len(self.sessions) > SESSION_CAPACITY:
            self.sessions.popitem(last=False)

    async def restore_sessions(self):
        if self._remembered is None:
            return
        now = int(time.time())
        rows = await run_db(self._remembered.active, now, SESSION_CAPACITY)
        epoch, steady = time.time(), time.monotonic()
        for digest, expires, uid, name in reversed(rows):
            if expires > epoch:
                self._cache_session(digest, steady + expires - epoch, Player(uid, name))

    def issue(self, player: Player) -> str:
        token = secrets.token_urlsafe(32)
        self._cache_session(self._session_digest(token), time.monotonic() + SESSION_SECONDS, player)
        return token

    async def remember(self, response, player):
        token = secrets.token_urlsafe(32)
        digest = self._session_digest(token)
        if self._remembered is not None:
            now = int(time.time())
            await run_db(self._remembered.add, digest, player.id, player.name, now + REMEMBER_SECONDS, now)
        self._cache_session(digest, time.monotonic() + REMEMBER_SECONDS, player)
        self.cookie(response, self.session_cookie, token, REMEMBER_SECONDS)

    async def revoke(self, request):
        digest = self._session_digest(self.get_token(request))
        if digest is None:
            return
        self.sessions.pop(digest, None)
        if self._remembered is not None:
            await run_db(self._remembered.remove, digest)

    def get_token(self, request):
        bearer = request.headers.get("Authorization", "")
        if bearer.startswith("Bearer "):
            return bearer.removeprefix("Bearer ")
        return request.cookies.get(self.session_cookie)

    def optional_session(self, request):
        key = self._session_digest(self.get_token(request))
        entry = self.sessions.get(key)
        if not entry or entry[0] <= time.monotonic():
            self.sessions.pop(key, None)
            return None
        return key, *entry

    def session(self, request):
        session = self.optional_session(request)
        if session is None:
            raise web.HTTPUnauthorized(text="Войдите через Telegram")
        return session

    def viewer_id(self, request) -> int | None:
        session = self.optional_session(request)
        return session[2].id if session else None

    def cookie(self, response, key, value, ttl):
        response.set_cookie(
            key, value, max_age=ttl, httponly=True, samesite="Lax",
            secure=self.public_url.startswith("https://"),
        )

    @property
    def redirect_url(self):
        return f"{self.public_url}/auth/callback"

    def start_login(self, request):
        if not self.login_enabled:
            raise web.HTTPServiceUnavailable(text="Вход через Telegram ещё не настроен")
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(32)
        invite = request.query.get("join", "")
        if len(invite) > 32 or not all(c.isascii() and (c.isalnum() or c in "-_") for c in invite):
            invite = ""
        friend = request.query.get("friend", "")
        if len(friend) > 640 or not all(c.isascii() and (c.isalnum() or c in "-_=") for c in friend):
            friend = ""
        destination = {key: value for key, value in (("join", invite), ("friend", friend)) if value}
        self.login_flows[state] = (time.monotonic() + LOGIN_SECONDS, verifier, destination)
        if len(self.login_flows) > 512:
            self.login_flows.popitem(last=False)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        query = urlencode({
            "client_id": self.client_id, "response_type": "code", "redirect_uri": self.redirect_url,
            "scope": "openid profile", "state": state, "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        response = web.Response(status=302, headers={"Location": f"{ISSUER}/auth?{query}"})
        self.cookie(response, LOGIN_COOKIE, state, LOGIN_SECONDS)
        return response

    async def finish_login(self, request, http):
        state, code = request.query.get("state", ""), request.query.get("code", "")
        cookie = request.cookies.get(LOGIN_COOKIE, "")
        if not state or not code or state != cookie or len(code) > 4096:
            raise web.HTTPBadRequest(text="Вход устарел. Откройте главную страницу и попробуйте снова.")
        flow = self.login_flows.pop(state, None)
        if flow is None or flow[0] <= time.monotonic():
            raise web.HTTPBadRequest(text="Вход устарел. Откройте главную страницу и попробуйте снова.")
        _, verifier, destination = flow
        import jwt

        try:
            async with http.post(
                f"{ISSUER}/token",
                headers={"Authorization": aiohttp.encode_basic_auth(self.client_id, self.client_secret)},
                data={"code": code, "client_id": self.client_id, "redirect_uri": self.redirect_url,
                      "grant_type": "authorization_code", "code_verifier": verifier},
            ) as result:
                result.raise_for_status()
                token = (await result.json())["id_token"]
            kid = jwt.get_unverified_header(token)["kid"]
            if time.monotonic() >= self._keys_expire or kid not in self._keys:
                async with http.get(f"{ISSUER}/.well-known/jwks.json") as result:
                    result.raise_for_status()
                    self._keys = {key["kid"]: key for key in (await result.json())["keys"]}
                    self._keys_expire = time.monotonic() + 3600
            claims = jwt.decode(
                token, jwt.PyJWK.from_dict(self._keys[kid], algorithm="RS256").key, algorithms=["RS256"],
                audience=self.client_id, issuer=ISSUER, options={"require": ["exp", "iat", "sub"]},
            )
            player = player_from_claims(claims)
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError, jwt.PyJWTError):
            raise web.HTTPUnauthorized(text="Telegram не подтвердил вход. Попробуйте ещё раз.") from None
        redirect = "/" + ("?" + urlencode(destination) if destination else "")
        response = web.Response(status=302, headers={"Location": redirect})
        await self.remember(response, player)
        response.del_cookie(LOGIN_COOKIE)
        return response
