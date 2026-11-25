"""Check application startup inside the release image against a local Telegram stub."""

import asyncio
import os
import re
from unittest.mock import patch

from aiohttp import ClientError, ClientSession, ClientTimeout, web
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

from app import main as application
from app.health import check_health


async def telegram(request):
    method = request.match_info["method"]
    if method == "getMe":
        result = {"id": 123456, "is_bot": True, "first_name": "Test bot", "username": "spyfall_test_bot"}
    elif method == "getUpdates":
        await asyncio.sleep(0.1)
        result = []
    elif method in {"setMyCommands", "setChatMenuButton"}:
        result = True
    else:
        raise AssertionError(f"Unexpected Bot API method: {method}")
    return web.json_response({"ok": True, "result": result})


async def smoke_test():
    url = "http://127.0.0.1:" + os.getenv("WEB_PORT", "8080")
    os.environ.update(
        BOT_TOKEN="123456:DOCKER_SMOKE_FIXTURE", PROXY_URL="", WEB_PUBLIC_URL=url, WEB_HOST="127.0.0.1",
    )
    stub = web.Application()
    stub.router.add_post("/bot{token}/{method}", telegram)
    runner = web.AppRunner(stub, access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        port = runner.addresses[0][1]
        api = TelegramAPIServer.from_base(f"http://127.0.0.1:{port}")

        def fixture_bot(token, session=None):
            return Bot(token, session=AiohttpSession(api=api))

        with patch.object(application, "Bot", fixture_bot):
            task = asyncio.create_task(application.main())
            try:
                async with ClientSession(timeout=ClientTimeout(total=2)) as client:
                    for _ in range(80):
                        if task.done():
                            await task
                            raise AssertionError("Bot stopped during startup")
                        if check_health()[0]:
                            break
                        await asyncio.sleep(0.25)
                    else:
                        raise AssertionError("Bot did not become healthy")

                    async with client.get(url + "/") as response:
                        response.raise_for_status()
                        index = await response.text()
                    bundle = re.search(r'<script[^>]+src="(/static/assets/[^"]+\.js)"', index)
                    assert bundle, "The image is missing the built frontend"
                    for path in (bundle[1], "/static/cosmetics/moustache-mask.webp"):
                        async with client.get(url + path) as response:
                            response.raise_for_status()
                            assert await response.read()
                    async with client.get(url + "/api/me") as response:
                        profile = await response.json()
                        assert profile["local_login"] and profile["bot"] == "spyfall_test_bot"
                    async with client.get(url + "/api/packs?scope=builtin") as response:
                        assert (await response.json())["total"] == 8
            except ClientError as error:
                raise AssertionError("Web interface did not respond") from error
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    finally:
        await runner.cleanup()
    print("Bot, web UI and eight imported starter packs passed the startup check.")


if __name__ == "__main__":
    asyncio.run(smoke_test())
