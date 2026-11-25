import asyncio
import hashlib
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from io import BytesIO

import aiohttp
from aiogram.exceptions import TelegramAPIError
from aiohttp import web

MAX_PHOTO_BYTES = 32 * 1024
MAX_AVATARS = 512
REFERENCE_SECONDS = 900


@dataclass
class AvatarEntry:
    user_id: int
    reference_until: float
    photo: bytes | None = None
    refresh_after: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class TelegramAvatars:
    def __init__(self, bot):
        self.bot = bot
        self.key = secrets.token_bytes(32)
        self.entries = OrderedDict()
        self.slots = asyncio.Semaphore(4)

    def link(self, user_id: int, scope: str) -> str | None:
        if user_id <= 0:
            return None
        reference = hashlib.blake2s(
            f"{scope}:{user_id}".encode(), key=self.key, digest_size=16,
        ).hexdigest()
        until = time.monotonic() + REFERENCE_SECONDS
        entry = self.entries.get(reference)
        if entry is None:
            self.entries[reference] = AvatarEntry(user_id, until)
            if len(self.entries) > MAX_AVATARS:
                self.entries.popitem(last=False)
        else:
            entry.reference_until = until
            self.entries.move_to_end(reference)
        return f"/avatar/{reference}"

    async def serve(self, request):
        entry = self.entries.get(request.match_info["reference"])
        if entry is None or entry.reference_until <= time.monotonic():
            return web.Response(status=404)
        async with entry.lock:
            if entry.refresh_after <= time.monotonic():
                await self.refresh(entry)
        age = max(0, min(300, int(entry.refresh_after - time.monotonic())))
        headers = {"Cache-Control": f"private, max-age={age}", "Cross-Origin-Resource-Policy": "same-origin"}
        if entry.photo is None:
            return web.Response(status=404, headers=headers)
        return web.Response(body=entry.photo, content_type="image/jpeg", headers=headers)

    async def refresh(self, entry: AvatarEntry):
        try:
            async with self.slots, asyncio.timeout(7):
                entry.photo = await self.download(entry.user_id)
            delay = 1800 if entry.photo else 300
        except (TelegramAPIError, aiohttp.ClientError, TimeoutError, OSError):
            delay = 60
        entry.refresh_after = time.monotonic() + delay

    async def download(self, user_id: int) -> bytes | None:
        photos = await self.bot.get_user_profile_photos(user_id, limit=1, request_timeout=4)
        sizes = [photo for photo in (photos.photos[0] if photos.photos else ())
                 if photo.file_size and photo.file_size <= MAX_PHOTO_BYTES]
        if not sizes:
            return None
        thumbnail = min(sizes, key=lambda photo: abs(max(photo.width, photo.height) - 160))
        file = await self.bot.get_file(thumbnail.file_id, request_timeout=4)
        if not file.file_path or (file.file_size and file.file_size > MAX_PHOTO_BYTES):
            return None
        destination = BytesIO()
        await self.bot.download_file(file.file_path, destination=destination, timeout=4)
        photo = destination.getvalue()
        return photo if len(photo) <= MAX_PHOTO_BYTES and photo.startswith(b"\xff\xd8\xff") else None
