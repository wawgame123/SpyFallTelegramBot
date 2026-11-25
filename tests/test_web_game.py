import asyncio
import hmac
import json
import re
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import AsyncMock

import jwt
import pytest
from aiogram.methods import DeleteMessage, SendMessage
from aiogram.types import File, PhotoSize, UserProfilePhotos
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives.asymmetric import rsa

from app.models.services.room_access import hash_room_password
from app.controllers.web.auth import COOKIE, ISSUER, REMEMBER_SECONDS, Player, validate_mini_app
from app.controllers.web.server import WebGame
from tests.test_bot_flow import env, run
from tests.test_stats_and_views import add_round, add_session

ORIGIN = "http://127.0.0.1"
TOKEN = "123456:TESTTOKEN"


def test_local_site_login_uses_telegram_identity_and_shows_telegram_lobby(env):
    async def scenario():
        game = WebGame(env.runtime, ORIGIN, TOKEN, packs=env.packs, stats=env.dp["stats"])
        already_owned = env.packs.create_pack(42, "Игрок", "Личный набор", ["Слово"])
        async with TestClient(TestServer(game.application())) as browser:
            profile = await (await browser.get("/api/me")).json()
            assert profile["local_login"] and profile["player"] is None
            await env.text(42, "/web")
            code = re.search(r"<code>([A-F0-9]{16})</code>", env.last_text()).group(1)
            request = {"code": code}
            assert (await browser.post("/api/auth/local", json=request)).status == 403
            origin = {"Origin": ORIGIN}
            assert (await browser.post("/api/auth/local", headers=origin, json={"code": "incorrect"})).status == 401
            assert not env.session.of(DeleteMessage)
            reply_id = env.session.sent_ids[-1]
            signed_in = await browser.post("/api/auth/local", headers=origin, json=request)
            assert signed_in.status == 200
            assert {(call.chat_id, call.message_id) for call in env.session.of(DeleteMessage)} == {(42, 11), (42, reply_id)}
            token = signed_in.cookies[game.auth.session_cookie].value
            headers = {"Authorization": f"Bearer {token}"}
            empty_profile = await (await browser.get("/api/profile", headers=headers)).json()
            assert empty_profile["name"] == "Имя42" and empty_profile["stats"] is None
            assert empty_profile["avatar"].startswith("/avatar/")
            assert (await browser.post("/api/auth/local", headers=origin, json=request)).status == 401
            assert len(env.session.of(DeleteMessage)) == 2
            visible = await (await browser.get("/api/packs", headers=headers)).json()
            assert any(pack["id"] == already_owned and pack["editable"] for pack in visible["packs"])
            await env.press(43, "online:create")
            room_id = env.service.room_id_of(43)
            room = env.service.get(room_id)
            listed = await (await browser.get("/api/lobbies", headers=headers)).json()
            assert any(item["code"] == room.code and not item["locked"] for item in listed["lobbies"])
            await env.runtime.close()

    run(scenario())


def test_browser_cookie_survives_restart_and_logout_stays_revoked(env):
    async def scenario():
        factory = env.dp["factory"]
        game = WebGame(env.runtime, ORIGIN, TOKEN, factory=factory)
        async with TestClient(TestServer(game.application())) as browser:
            code, _ = game.auth.local_code(Player(42, "Игрок"), 42, 1)
            response = await browser.post("/api/auth/local", headers={"Origin": ORIGIN}, json={"code": code})
            assert response.status == 200
            cookie = response.cookies[game.auth.session_cookie]
            assert cookie["httponly"] and cookie["samesite"] == "Lax"
            assert int(cookie["max-age"]) == REMEMBER_SECONDS
            assert cookie.value not in game.auth.sessions
            headers = {"Cookie": f"{cookie.key}={cookie.value}", "Origin": ORIGIN}

        for site in ("http://localhost", ORIGIN):
            restored = WebGame(env.runtime, site, TOKEN, factory=factory)
            async with TestClient(TestServer(restored.application())) as browser:
                profile = await (await browser.get("/api/me", headers=headers)).json()
                if site != ORIGIN:
                    assert profile["player"] is None
                else:
                    assert profile["player"]["name"] == "Игрок"
                    assert (await browser.post("/api/logout", headers=headers)).status == 200

        restored = WebGame(env.runtime, ORIGIN, TOKEN, factory=factory)
        async with TestClient(TestServer(restored.application())) as browser:
            assert (await (await browser.get("/api/me", headers=headers)).json())["player"] is None

    run(scenario())


def test_local_login_removes_reply_even_if_telegram_response_is_delayed(env, monkeypatch):
    async def scenario():
        game = WebGame(env.runtime, ORIGIN, TOKEN)
        delivered, acknowledge = asyncio.Event(), asyncio.Event()
        original = env.session.make_request

        async def delayed(bot, method, timeout=None):
            result = await original(bot, method, timeout)
            if isinstance(method, SendMessage):
                delivered.set()
                await acknowledge.wait()
            return result

        monkeypatch.setattr(env.session, "make_request", delayed)
        async with TestClient(TestServer(game.application())) as browser:
            sending = asyncio.create_task(env.text(42, "/web"))
            try:
                await asyncio.wait_for(delivered.wait(), 2)
                code = re.search(r"<code>([A-F0-9]{16})</code>", env.last_text()).group(1)
                login = await browser.post("/api/auth/local", headers={"Origin": ORIGIN}, json={"code": code})
                assert login.status == 200
                assert [call.message_id for call in env.session.of(DeleteMessage)] == [11]
            finally:
                acknowledge.set()
                await sending
            assert [call.message_id for call in env.session.of(DeleteMessage)] == [11, env.session.sent_ids[-1]]

    run(scenario())


def test_web_avatars_load_telegram_thumbnail_and_cache_per_scope(env, monkeypatch):
    jpeg = b"\xff\xd8\xff-test"
    photos = AsyncMock(return_value=UserProfilePhotos(total_count=1, photos=[[
        PhotoSize(file_id="thumbnail", file_unique_id="photo", width=160, height=160, file_size=len(jpeg)),
    ]]))
    file = AsyncMock(return_value=File(file_id="thumbnail", file_unique_id="photo",
                                      file_size=len(jpeg), file_path="photos/small.jpg"))

    async def download(path, *, destination, timeout):
        assert path == "photos/small.jpg"
        destination.write(jpeg)

    monkeypatch.setattr(env.bot, "get_user_profile_photos", photos)
    monkeypatch.setattr(env.bot, "get_file", file)
    monkeypatch.setattr(env.bot, "download_file", download)

    async def scenario():
        game = WebGame(env.runtime, ORIGIN, TOKEN, packs=env.packs, stats=env.dp["stats"])
        user_id = 987654321
        headers = {"Authorization": f"Bearer {game.auth.issue(Player(user_id, 'Игрок'))}"}
        env.service.create(user_id, "Игрок", listed=True)
        async with TestClient(TestServer(game.application())) as browser:
            me = await (await browser.get("/api/me", headers=headers)).json()
            profile = await (await browser.get("/api/profile", headers=headers)).json()
            assert me["player"]["avatar"] == profile["avatar"]
            assert (await browser.get("/avatar/" + "a" * 32)).status == 404
            photos.assert_not_awaited()
            for _ in range(2):
                response = await browser.get(profile["avatar"])
                assert response.status == 200 and response.content_type == "image/jpeg"
                assert await response.read() == jpeg
            photos.assert_awaited_once_with(user_id, limit=1, request_timeout=4)
            lobbies = await (await browser.get("/api/lobbies")).json()
            assert str(user_id) not in json.dumps(lobbies)
            face, = lobbies["lobbies"][0]["avatars"]
            assert face["initial"] == "И" and face["image"] != profile["avatar"]
            photos.return_value = UserProfilePhotos(total_count=0, photos=[])
            for _ in range(2):
                assert (await browser.get(face["image"])).status == 404
            assert photos.await_count == 2

    run(scenario())


def test_web_friends_are_personal_and_prioritize_public_lobbies(env):
    async def scenario():
        factory = env.dp["factory"]
        game = WebGame(env.runtime, ORIGIN, TOKEN, factory=factory)
        first = env.service.create(81, "Друг", listed=True)
        private = env.service.create(82, "Частная комната")
        env.service.join_code(84, "Участник", first.room.code)
        public = env.service.create(83, "Другое лобби", listed=True)

        def headers(user_id, name):
            return {"Origin": ORIGIN, "Authorization": "Bearer " + game.auth.issue(Player(user_id, name))}

        owner = headers(42, "Зритель")
        member = headers(84, "Участник")
        async with TestClient(TestServer(game.application())) as browser:
            async def listed(headers=None):
                return (await (await browser.get("/api/lobbies", headers=headers)).json())["lobbies"]

            default = await listed()
            assert [row["code"] for row in default] == [public.room.code, first.room.code]
            assert all(not row["friends"] for row in default)
            assert all("friend_ref" not in face for row in default for face in row["avatars"])
            assert (await browser.get("/api/friends")).status == 401
            assert (await browser.post("/api/friends", headers={"Origin": ORIGIN}, json={})).status == 401

            member_lobby = await (await browser.get("/api/room", headers=member)).json()
            assert [seat["name"] for seat in member_lobby["room"]["lobby"]["players"]] == ["Друг", "Участник"]
            own = await (await browser.get("/api/friends", headers=member)).json()
            own_ref = own["invite"]
            assert (await browser.post("/api/friends", headers=member, json={"ref": own_ref, "add": True})).status == 400
            assert (await browser.post("/api/friends", headers=owner, json={"ref": "invalid", "add": True})).status == 400
            for _ in range(2):
                response = await browser.post("/api/friends", headers=owner, json={"ref": own_ref, "add": True})
                assert response.status == 200
            friend_list = await (await browser.get("/api/friends", headers=owner)).json()
            assert [friend["name"] for friend in friend_list["friends"]] == ["Участник"]
            assert (await (await browser.get("/api/friends", headers=member)).json())["friends"] == []
            featured = await listed(owner)
            assert [row["code"] for row in featured] == [first.room.code, public.room.code]
            assert featured[0]["friends"] == ["Участник"]
            private_ref = game.friends.reference(82, "Частная комната")
            await browser.post("/api/friends", headers=owner, json={"ref": private_ref, "add": True})
            assert private.room.code not in [row["code"] for row in await listed(owner)]
            assert (await browser.post("/api/friends", headers=owner, json={"ref": own_ref, "add": False})).status == 200
            assert [row["code"] for row in await listed(owner)] == [public.room.code, first.room.code]

        replacement = WebGame(env.runtime, ORIGIN, TOKEN, factory=factory)
        assert replacement.friends.decode(own_ref) == [84, "Участник"]
        assert replacement.friends.repository.list(42) == {82: "Частная комната"}

    run(scenario())


def test_web_catalog_uses_shared_rating_and_pack_permissions(env):
    async def scenario():
        game = WebGame(env.runtime, ORIGIN, TOKEN, packs=env.packs, stats=env.dp["stats"])
        owner = {"Authorization": f"Bearer {game.auth.issue(Player(42, 'Игрок'))}", "Origin": ORIGIN}
        guest = {"Authorization": f"Bearer {game.auth.issue(Player(43, 'Гость'))}", "Origin": ORIGIN}
        factory = env.dp["factory"]
        add_round(factory, add_session(factory), 1, 42, {42: ("win", 3), 43: ("loss", 0)})
        async with TestClient(TestServer(game.application())) as browser:
            anonymous = {"Origin": ORIGIN}
            ranking = await (await browser.get("/api/ranking")).json()
            assert ranking["rows"][0]["points"] == 3
            assert not any(row["mine"] for row in ranking["rows"])
            assert ranking["own_rank"] is None
            ranking = await (await browser.get("/api/ranking?sort=spy&period=week", headers=owner)).json()
            assert ranking["rows"][0]["mine"] and ranking["rows"][0]["spy"] == 1
            assert "user_id" not in ranking["rows"][0]
            assert (await browser.get("/api/profile")).status == 401
            profile = await (await browser.get("/api/profile", headers=owner)).json()
            assert profile["name"] == "Игрок" and profile["stats"]["games"] == 1
            assert profile["stats"]["points"] == 3 and profile["stats"]["spy_wins"] == 1
            assert "user_id" not in profile
            builtin = await (await browser.get("/api/packs?scope=builtin")).json()
            assert builtin["packs"] and not any(pack["editable"] for pack in builtin["packs"])
            for scope in ("my", "fav"):
                assert (await browser.get("/api/packs?scope=" + scope)).status == 401

            payload = {"title": "На орбите", "words": "Созвездие, Спутник", "public": False}
            assert (await browser.post("/api/packs", headers=anonymous, json=payload)).status == 401
            blocked = await browser.post("/api/packs", headers={**owner, "Origin": "https://elsewhere.example"},
                                         json=payload)
            assert blocked.status == 403
            invalid = await browser.post("/api/packs", headers=owner, json={**payload, "public": True})
            assert invalid.status == 422
            assert env.packs.list_packs(42, "my")[1] == 0
            created = await browser.post("/api/packs", headers=owner, json=payload)
            assert created.status == 201
            pack_id = (await created.json())["id"]
            path = f"/api/packs/{pack_id}"
            assert (await browser.get(path, headers=guest)).status == 404
            assert (await browser.get(path)).status == 404
            listing = await (await browser.get("/api/packs?q=На орбите")).json()
            assert listing["total"] == 0
            own = await (await browser.get(path, headers=owner)).json()
            assert own["words"] == ["Созвездие", "Спутник"] and own["editable"]
            denied = await browser.post(path, headers=guest, json={**payload, "revision": own["revision"]})
            assert denied.status == 422
            assert (await browser.post(path, headers=anonymous, json=payload)).status == 401

            env.packs.add_words(42, pack_id, ["Луна"])
            stale = await browser.post(path, headers=owner, json={**payload, "revision": own["revision"]})
            assert stale.status == 409
            latest = await (await browser.get(path, headers=owner)).json()
            changed = await browser.post(path, headers=owner, json={
                "title": "Космос", "public": True, "revision": latest["revision"],
                "words": "Созвездие\n" + "\n".join(f"Объект {i}" for i in range(10)),
            })
            assert changed.status == 200
            assert env.packs.info(42, pack_id).title == "Космос"
            public = await (await browser.get(path)).json()
            assert public["public"] and not public["editable"] and "revision" not in public
            assert len(public["words"]) == 11 and "Спутник" not in public["words"]
            found = await (await browser.get("/api/packs?scope=public&q=Космос")).json()
            assert found["total"] == 1 and found["packs"][0]["id"] == pack_id
            favorite = path + "/favorite"
            assert (await browser.post(favorite, headers=anonymous, json={})).status == 401
            saved = await (await browser.post(favorite, headers=guest, json={})).json()
            assert saved["favorite"] is True
            assert env.packs.info(43, pack_id).is_favorite
            public = await (await browser.get(path)).json()
            assert not public["favorite"]
            removed = await (await browser.post(favorite, headers=guest, json={})).json()
            assert removed["favorite"] is False
            await env.runtime.close()

    run(scenario())


def signed_data(uid=2, date=None):
    values = {"auth_date": str(int(time.time()) if date is None else date),
              "user": json.dumps({"id": uid, "first_name": "Telegram"})}
    key = hmac.digest(b"WebAppData", TOKEN.encode(), "sha256")
    payload = "\n".join(f"{k}={v}" for k, v in sorted(values.items()))
    values["hash"] = hmac.digest(key, payload.encode(), "sha256").hex()
    return urlencode(values)


def test_avatar_cosmetics_are_free_persistent_and_visible_in_the_lobby(env):
    async def scenario():
        factory = env.dp["factory"]
        game = WebGame(env.runtime, ORIGIN, TOKEN, factory=factory)
        lobby = env.service.create(42, "Игрок", listed=True, via_web=True)
        env.service.join_code(43, "Гость", lobby.room.code, via_web=True)
        owner = {"Authorization": f"Bearer {game.auth.issue(Player(42, 'Игрок'))}", "Origin": ORIGIN}
        guest = {"Authorization": f"Bearer {game.auth.issue(Player(43, 'Гость'))}"}
        look = {"hat": "fedora", "face": "cat"}

        async with TestClient(TestServer(game.application())) as browser:
            catalog = await (await browser.get("/api/cosmetics")).json()
            assert {slot: len(items) for slot, items in catalog.items()} == {"hat": 16, "face": 18, "outfit": 5}
            assert (await browser.post("/api/cosmetics", headers={"Origin": ORIGIN}, json=look)).status == 401
            for invalid in ({"hat": "cat", "face": None}, {**look, "user_id": 43}):
                assert (await browser.post("/api/cosmetics", headers=owner, json=invalid)).status == 400
            before = (await (await browser.get("/api/room", headers=guest)).json())["room"]
            stream = await browser.get("/api/room/events", headers=guest)
            assert await asyncio.wait_for(stream.content.readexactly(14), 2) == b"data: change\n\n"
            saved = await browser.post("/api/cosmetics", headers=owner, json=look)
            assert await saved.json() == {"cosmetics": look}
            assert await asyncio.wait_for(stream.content.readexactly(14), 2) == b"data: change\n\n"
            stream.close()
            after = (await (await browser.get("/api/room", headers=guest)).json())["room"]
            assert after["revision"] == before["revision"]
            assert after["appearance_revision"] != before["appearance_revision"]
            assert [member["cosmetics"] for member in after["lobby"]["players"]] == [look, None]
            directory = await (await browser.get("/api/lobbies")).json()
            assert directory["lobbies"][0]["avatars"][0]["cosmetics"] == look
            assert (await WebGame(env.runtime, ORIGIN, TOKEN, factory=factory).cosmetics.equipped([42]))[42] == look
            placement = {
                "hat": {"arc": -62, "rotation": 35, "scale": 125, "mirror": True},
                "face": {"x": -14, "y": 19, "rotation": -60, "scale": 85, "mirror": False},
            }
            moved = {**look, "face": "moustache-mask", "outfit": "spy-trench", "placement": placement}
            assert (await browser.post("/api/cosmetics", headers=owner, json=moved)).status == 200
            current = (await (await browser.get("/api/room", headers=guest)).json())["room"]
            assert current["lobby"]["players"][0]["cosmetics"] == moved
            invalid_positions = [
                ("hat", {"arc": 90}), ("hat", {"rotation": 181}), ("hat", {"scale": 300}),
                ("face", {"x": 42, "y": 42}), ("face", {"mirror": 1}),
            ]
            for slot, changes in invalid_positions:
                invalid = {**placement, slot: {**placement[slot], **changes}}
                assert (await browser.post("/api/cosmetics", headers=owner, json={
                    **look, "placement": invalid,
                })).status == 400
            assert (await game.cosmetics.equipped([42]))[42] == moved
            assert (await browser.post("/api/cosmetics", headers=owner, json={**look, "outfit": "crown"})).status == 400
            assert (await browser.post("/api/cosmetics", headers=owner, json=look)).status == 200
            assert (await game.cosmetics.equipped([42]))[42]["outfit"] == "spy-trench"
            clear = await browser.post("/api/cosmetics", headers=owner, json={"hat": None, "face": None, "outfit": None})
            assert clear.status == 200
            assert not await game.cosmetics.equipped([42])

    run(scenario())


def test_lobby_chat_is_shared_by_browser_and_telegram(env):
    async def scenario():
        game = WebGame(env.runtime, ORIGIN, TOKEN)

        def headers(uid):
            key = game.auth.issue(Player(uid, f"Browser {uid}"))
            return {"Authorization": f"Bearer {key}", "Origin": ORIGIN}

        async with TestClient(TestServer(game.application())) as browser:
            host = headers(1)
            assert (await browser.post("/api/lobbies", headers=host, json={})).status == 200
            rid = env.service.room_id_of(1)
            await env.text(2, f"/start join_{env.service.get(rid).token}")
            stream = await browser.get("/api/room/events", headers=host)
            assert await asyncio.wait_for(stream.content.readexactly(14), 2) == b"data: change\n\n"
            payload = {"room": rid, "phase": "lobby", "text": "Привет\nИз браузера"}
            assert (await browser.post("/api/room/chat", headers=headers(3), json=payload)).status == 409
            assert (await browser.post("/api/room/chat", headers=host, json=payload)).status == 200
            assert await asyncio.wait_for(stream.content.readexactly(14), 2) == b"data: change\n\n"
            stream.close()
            await env.runtime.refresh(rid)
            assert "Привет\nИз браузера" in env.last_text()

            await env.text(2, "Привет из Telegram")
            room = (await (await browser.get("/api/room", headers=host)).json())["room"]
            assert room["can_chat"]
            browser_message, telegram_message = room["lobby"]["activity"][-2:]
            assert browser_message[:2] == ["Browser 1", "Привет\nИз браузера"]
            assert telegram_message[:2] == [env.service.get(rid).name_of("2"), "Привет из Telegram"]
            for message in (browser_message, telegram_message):
                assert message[2].startswith("/avatar/")

    run(scenario())


@pytest.mark.parametrize("hidden", [False, True])
def test_web_lobby_joins_telegram_room_and_keeps_role_private(env, hidden):
    async def scenario():
        game = WebGame(env.runtime, ORIGIN, TOKEN)
        def headers(uid):
            key = game.auth.issue(Player(uid, f"Browser {uid}"))
            return {"Authorization": f"Bearer {key}", "Origin": ORIGIN}

        async with TestClient(TestServer(game.application())) as browser:
            index = await browser.get("/")
            assert index.status == 200
            bundle = re.search(r'<script[^>]+src="(/static/assets/[^"]+\.js)"', await index.text())
            assert bundle
            assert (await browser.get(bundle[1])).status == 200
            assert (await browser.get("/api/lobbies")).status == 200
            assert (await browser.get("/api/room")).status == 401
            assert (await browser.post("/api/lobbies", headers={"Origin": ORIGIN}, json={})).status == 401
            assert (await browser.post("/api/join", headers={"Origin": ORIGIN}, json={})).status == 401
            host, guest = headers(1), headers(3)
            assert (await browser.post("/api/lobbies", headers={"Authorization": host["Authorization"]},
                                       json={"listed": True})).status == 403
            created = await browser.post("/api/lobbies", headers=host, json={"listed": True})
            assert created.status == 200
            rid = env.service.room_id_of(1)
            room = env.service.get(rid)
            private = env.service.create(5, "Private")
            locked = env.service.create(6, "Locked", listed=True, password_hash=hash_room_password("correct"))
            game.lobbies_until = 0
            result = await (await browser.get("/api/lobbies")).json()
            rows = {row["code"]: row for row in result["lobbies"]}
            assert room.code in rows and not rows[room.code]["locked"]
            assert private.room.code not in rows and rows[locked.room.code]["locked"]
            assert "password_hash" not in json.dumps(rows)
            req = await browser.post("/api/join", headers=guest, json={"code": locked.room.code})
            assert req.status == 409 and (await req.json())["password"]
            req = await browser.post("/api/join", headers=guest, json={"code": locked.room.code, "password": "wrong"})
            assert req.status == 409
            await env.text(2, f"/start join_{room.token}")
            assert env.service.room_id_of(2) == rid
            gtk = env.service.panel_id_of(2)
            req = await browser.post("/api/join", headers=guest, json={"code": room.code})
            assert req.status == 200
            stream = await browser.get("/api/room/events", headers=host)
            assert stream.status == 200
            assert await asyncio.wait_for(stream.content.readline(), 2) == b"data: change\n"
            await stream.content.readline()
            await env.press(2, "rm:rd:1", message_id=gtk)
            assert await asyncio.wait_for(stream.content.readline(), 2) == b"data: change\n"
            stream.close()

            async def view(identity):
                return (await (await browser.get("/api/room", headers=identity)).json())["room"]

            async def action(identity, value, snapshot=None):
                snapshot = snapshot or await view(identity)
                return await browser.post("/api/room/action", headers=identity, json={
                    "room": snapshot["id"], "revision": snapshot["revision"], "action": value,
                })

            snapshot = await view(guest)
            assert (await action(guest, "rd:1", snapshot)).status == 200
            assert (await action(guest, "rd:0", snapshot)).status == 409
            assert (await action(host, "ui:settings")).status == 200
            assert (await action(guest, "csv:max:5")).status == 409
            assert (await action(host, "csv:max:2")).status == 409
            assert (await action(host, "csv:max:5")).status == 200
            assert (await action(host, "csv:rounds:0")).status == 409
            assert (await action(host, "csv:rounds:17")).status == 200
            assert (await view(host))["settings"]["rounds"]["value"] == "17"
            if hidden:
                assert (await action(host, "mode:hidden")).status == 200
            assert (await action(host, "ui:themes")).status == 200
            assert (await action(host, "th:animals")).status == 200
            assert (await action(host, "ui:settings")).status == 200
            assert (await action(host, "ui:lobby")).status == 200
            assert (await action(host, "start")).status == 200
            room = env.service.get(rid)
            assert room.phase == "dealing"
            roles = [(1, host), (3, guest)]
            for uid, identity in roles:
                visible = await view(identity)
                assert room.round.word not in visible["text"]
                assert "password_hash" not in json.dumps(visible)
                role = await (await action(identity, "role")).json()
                if hidden:
                    assert role["alert"] == "Ваше слово:\n\n" + room.round.word_for(str(uid)).upper()
                elif str(uid) == room.round.spy_id:
                    assert "Вы шпион" in role["alert"]
                else:
                    assert room.round.word.upper() in role["alert"]
            await env.press(2, "rm:role", message_id=gtk)
            playing = env.service.get(rid)
            assert playing.phase == "playing"
            assert (await view(host))["discussion"]["theme"] == playing.round.theme
            identities = {1: host, 2: headers(2), 3: guest}
            spy = int(playing.round.spy_id)
            other = next(uid for uid in identities if uid != spy)
            assert (await view(identities[other]))["guess"] is None
            assert (await action(identities[other], "gx:2:1")).status == 409
            if not hidden:
                if int(playing.current()) == spy:
                    assert (await action(identities[spy], "endturn:0")).status == 200
                author = int(env.service.get(rid).current())
                assert (await browser.post("/api/room/chat", headers=identities[author], json={
                    "room": rid, "text": "Первая подсказка",
                })).status == 200
                assert (await action(identities[spy], "gu")).status == 200
                sheet = (await view(identities[spy]))["guess"]
                index = len(sheet["words"]) - 1
                assert index >= 8
                assert (await action(identities[spy], f"gx:{index}:1")).status == 200
                assert (await view(identities[spy]))["guess"]["crossed"] == [index]
                assert (await view(identities[other]))["guess"] is None
                assert (await action(identities[spy], f"gp:{index}")).status == 409
                assert (await action(identities[spy], f"gx:{index}:0")).status == 200
                assert (await action(identities[spy], f"gp:{index}")).status == 200
                assert (await action(identities[spy], "no")).status == 200
                assert (await action(identities[spy], "cancel")).status == 200

            while env.service.get(rid).round.phase.value == "discussion":
                current = int(env.service.get(rid).round.current())
                if current == 2:
                    await env.press(2, "rm:endturn:1", message_id=gtk)
                else:
                    identity = host if current == 1 else guest
                    assert (await action(identity, "endturn:1")).status == 200
            spy = env.service.get(rid).round.spy_id
            for uid in (1, 2, 3):
                target = spy if str(uid) != spy else next(str(i) for i in (1, 2, 3) if i != uid)
                if uid == 2:
                    await env.press(2, f"rm:v:{target}", message_id=gtk)
                else:
                    identity = host if uid == 1 else guest
                    assert (await action(identity, f"v:{target}")).status == 200
            assert "Итоги раунда" in (await view(host))["text"]
            assert "Итоги раунда" in (await view(guest))["text"]
            assert (await action(host, "end")).status == 200
            code = env.service.get(rid).code
            assert (await action(host, f"rematch:{code}")).status == 200
            assert env.service.get(rid).phase == "lobby"
            assert env.service.panel_id_of(1) is None
            assert env.service.panel_id_of(3) is None
            await env.runtime.close()

    run(scenario())


def test_mini_auth_and_telegram_password_invitation(env):
    async def scenario():
        game = WebGame(env.runtime, ORIGIN, TOKEN)
        room = env.service.create(1, "Host", password_hash=hash_room_password("code-7788"))
        async with TestClient(TestServer(game.application())) as browser:
            data = signed_data()
            result = await browser.post("/api/auth/mini", headers={"Origin": ORIGIN}, json={"initData": data})
            key = (await result.json())["session"]
            profile = await browser.get("/api/me", headers={"Authorization": f"Bearer {key}"})
            assert (await profile.json())["player"]["name"] == "Telegram"
            assert validate_mini_app(data, TOKEN).name == "Telegram"
            expired = signed_data(date=int(time.time()) - 700)
            assert (await browser.post("/api/auth/mini", headers={"Origin": ORIGIN},
                                       json={"initData": expired})).status == 401
            await env.text(2, f"/start join_{room.room.token}")
            assert env.service.room_id_of(2) is None
            await env.text(2, "code-7788")
            assert env.service.room_id_of(2) == room.room_id
            await env.runtime.close()

    run(scenario())


class OAuthResponse:
    def __init__(self, result):
        self.result = result

    def raise_for_status(self):
        pass

    async def json(self):
        return self.result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def test_oidc_uses_telegram_id_and_pkce_with_signed_token(env):
    async def scenario():
        game = WebGame(env.runtime, ORIGIN, TOKEN, "1000", "mock-secret")
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        jwk["kid"] = "fixture"
        claims = {"iss": ISSUER, "aud": "1000", "sub": "different-oidc-identifier", "id": 42,
                  "iat": int(time.time()), "exp": int(time.time()) + 60, "name": "Player"}
        token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "fixture"})

        class OAuth:
            def post(self, url, *, headers, data):
                assert url == ISSUER + "/token"
                assert data["code_verifier"] and headers["Authorization"].startswith("Basic ")
                return OAuthResponse({"id_token": token})

            def get(self, url):
                assert url == ISSUER + "/.well-known/jwks.json"
                return OAuthResponse({"keys": [jwk]})

        async with TestClient(TestServer(game.application())) as browser:
            game.http = OAuth()
            result = await browser.get("/auth/login?join=invite_123&friend=friend_ref%3D", allow_redirects=False)
            assert result.status == 302
            params = parse_qs(urlsplit(result.headers["Location"]).query)
            assert params["code_challenge_method"] == ["S256"]
            state = params["state"][0]
            callback = "/auth/callback?" + urlencode({"state": state, "code": "test"})
            result = await browser.get(callback, headers={"Cookie": f"spyfall_login={state}"}, allow_redirects=False)
            assert result.status == 302
            assert parse_qs(urlsplit(result.headers["Location"]).query) == {
                "join": ["invite_123"], "friend": ["friend_ref="],
            }
            session = result.cookies[COOKIE].value
            profile = await browser.get("/api/me", headers={"Authorization": f"Bearer {session}"})
            assert (await profile.json())["player"]["name"] == "Player"
            duplicate = await browser.get(callback, headers={"Cookie": f"spyfall_login={state}"}, allow_redirects=False)
            assert duplicate.status == 400

    run(scenario())
