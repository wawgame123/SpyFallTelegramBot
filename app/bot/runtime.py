from app.database.worker import run_db
import asyncio
import logging
import time
from collections import OrderedDict
from weakref import WeakValueDictionary

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from app.bot.ui import delete_quietly
from app.bot.confirmations import LeaveConfirmations
from app.game.room import Room
from app.game.session import Screen
from app.services.room_service import RoomService

log = logging.getLogger(__name__)
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def to_room_markup(screen: Screen) -> InlineKeyboardMarkup:
    rows = []
    for row in screen.rows:
        buttons = []
        for label, action in row:
            if action.startswith("url:"):
                buttons.append(InlineKeyboardButton(text=label, url=action[4:]))
            elif action.startswith("copy:"):
                buttons.append(
                    InlineKeyboardButton(
                        text=label,
                        copy_text=CopyTextButton(text=action[5:]),
                    )
                )
            else:
                buttons.append(InlineKeyboardButton(text=label, callback_data=f"rm:{action}"))
        rows.append(buttons)
    return InlineKeyboardMarkup(inline_keyboard=rows)


class OnlineRuntime:
    def __init__(self, bot: Bot, service: RoomService, bot_username: str, maintenance=(), prefs_for=None):
        self.bot = bot
        self.service = service
        self.bot_username = bot_username
        self._locks = WeakValueDictionary()
        self._last_refresh: dict[int, float] = {}
        self._maintenance = list(maintenance)
        self._prefs_for = prefs_for
        self._render_slots = asyncio.Semaphore(4)
        self._panel_cache: dict[int, tuple[tuple, float]] = {}
        self._retry_after: dict[int, float] = {}
        self._pending_rooms: dict[int, int] = {}
        self.last_cycle = time.monotonic()
        self.edit_recovery = None
        self.cache_ttl = 60.0
        self.leave_confirmations = LeaveConfirmations()
        self._last_chat = OrderedDict()

    def allow_chat_message(self, user_id, interval=1.0):
        now = time.monotonic()
        previous = self._last_chat.get(user_id)
        if previous is not None and now - previous < interval:
            return False
        self._last_chat.pop(user_id, None)
        if len(self._last_chat) >= 4096:
            self._last_chat.popitem(last=False)
        self._last_chat[user_id] = now
        return True

    def _defer_render(self, uid, seconds, room_id):
        if uid not in self._retry_after and len(self._retry_after) >= 4096:
            oldest = next(iter(self._retry_after))
            self._retry_after.pop(oldest)
            self._pending_rooms.pop(oldest, None)
        self._retry_after[uid] = time.monotonic() + max(1.0, seconds)
        self._pending_rooms[uid] = room_id

    def _remember(self, uid: int, signature: tuple) -> None:
        if uid not in self._panel_cache and len(self._panel_cache) >= 4096:
            self._panel_cache.pop(next(iter(self._panel_cache)))
        self._panel_cache[uid] = (signature, time.monotonic())
        self._retry_after.pop(uid, None)
        self._pending_rooms.pop(uid, None)

    def lock(self, room_id: int) -> asyncio.Lock:
        return self._locks.setdefault(room_id, asyncio.Lock())

    def in_room(self, user_id: int) -> bool:
        return self.service.room_id_of(user_id) is not None

    async def adopt_panel(self, room_id: int, user_id: int, message_id: int) -> None:
        self._panel_cache.pop(user_id, None)
        self._retry_after.pop(user_id, None)
        self._pending_rooms.pop(user_id, None)
        room = (await run_db(self.service.get, room_id))
        old = None
        if room is not None:
            for player in room.players:
                if player["id"] == user_id:
                    old = player.get("panel")
        if old and old != message_id:
            await delete_quietly(self.bot, user_id, old)
        (await run_db(self.service.set_panel, room_id, user_id, message_id, player_activity=True))

    async def refresh(self, room_id: int) -> None:
        room = (await run_db(self.service.get, room_id))
        if room is None:
            return
        self._last_refresh[room_id] = time.monotonic()
        now = time.time()
        async def render(player):
            async with self._render_slots:
                await self._render(room_id, room, player, now)
        await asyncio.gather(*(render(player) for player in list(room.players)))

    async def _render(self, room_id: int, room: Room, player: dict, now: float) -> None:
        uid = player["id"]
        screen = room.view_for(uid, self.bot_username, now)
        markup = to_room_markup(screen)
        styles = None
        if self._prefs_for is not None:
            prefs = (await run_db(self._prefs_for, uid))
            styles = (prefs.emoji, prefs.colors)
        markup_json = markup.model_dump_json(exclude_none=True)
        signature = (room_id, player.get("panel"), screen.text, markup_json, styles)
        current = time.monotonic()
        if self._retry_after.get(uid, 0) > current:
            return
        cached = self._panel_cache.get(uid)
        if cached and cached[0] == signature and current - cached[1] < self.cache_ttl:
            return
        if player.get("panel"):
            try:
                await self.bot.edit_message_text(
                    screen.text,
                    chat_id=uid,
                    message_id=player["panel"],
                    reply_markup=markup,
                    parse_mode="HTML",
                    link_preview_options=NO_PREVIEW,
                )
                self._remember(uid, signature)
                return
            except TelegramBadRequest as err:
                reason = str(err).lower()
                if "not modified" in reason:
                    self._remember(uid, signature)
                    return
                if not any(marker in reason for marker in ("message to edit not found", "message can't be edited")):
                    self._retry_after.pop(uid, None)
                    self._pending_rooms.pop(uid, None)
                    log.warning("Panel %s edit rejected: %s", uid, err)
                    return
            except TelegramRetryAfter as err:
                self._defer_render(uid, err.retry_after, room_id)
                log.warning("Panel %s rate limited for %s seconds", uid, err.retry_after)
                return
            except TelegramForbiddenError:
                self._retry_after.pop(uid, None)
                self._pending_rooms.pop(uid, None)
                return
            except TelegramNetworkError as err:
                self._defer_render(uid, 5, room_id)
                log.warning("Сеть: не удалось обновить панель %s: %s", uid, err)
                return
            except TelegramAPIError:
                self._defer_render(uid, 10, room_id)
                log.exception("edit failed")
                return
        try:
            sent = await self.bot.send_message(
                uid,
                screen.text,
                reply_markup=markup,
                parse_mode="HTML",
                link_preview_options=NO_PREVIEW,
            )
            (await run_db(self.service.set_panel, room_id, uid, sent.message_id))
            self._remember(uid, (room_id, sent.message_id, screen.text, markup_json, styles))
        except TelegramRetryAfter as err:
            self._defer_render(uid, err.retry_after, room_id)
            log.warning("Panel %s rate limited for %s seconds", uid, err.retry_after)
        except TelegramNetworkError as err:
            self._defer_render(uid, 5, room_id)
            log.warning("Сеть: не удалось отправить панель %s: %s", uid, err)
        except (TelegramForbiddenError, TelegramBadRequest):
            self._retry_after.pop(uid, None)
            self._pending_rooms.pop(uid, None)
        except TelegramAPIError:
            self._defer_render(uid, 10, room_id)
            log.exception("send failed")

    async def retry_pending(self) -> None:
        """Retry even lobby/result screens, always using current membership/state."""
        now = time.monotonic()
        due = {rid for uid, rid in list(self._pending_rooms.items())
               if self._retry_after.get(uid, 0) <= now}
        for room_id in due:
            async with self.lock(room_id):
                room = await run_db(self.service.get, room_id)
                members = {p["id"] for p in room.players} if room else set()
                for uid, rid in list(self._pending_rooms.items()):
                    if rid == room_id and uid not in members:
                        self._pending_rooms.pop(uid, None)
                        self._retry_after.pop(uid, None)
                        self._panel_cache.pop(uid, None)
                if room:
                    await self.refresh(room_id)

    async def tick(self) -> None:
        for room_id in (await run_db(self.service.due_room_ids)):
            async with self.lock(room_id):
                update = (await run_db(self.service.tick_room, room_id))
                if update and update.changed:
                    await self.refresh(room_id)

    async def countdown(self, now: float | None = None, every: float = 10.0) -> int:
        current = time.monotonic() if now is None else now
        active = (await run_db(self.service.active_room_ids))
        self._last_refresh = {k: v for k, v in self._last_refresh.items() if k in active}
        refreshed = 0
        for room_id in active:
            last = self._last_refresh.get(room_id)
            if last is None or current - last >= every:
                async with self.lock(room_id):
                    await self.refresh(room_id)
                    self._last_refresh[room_id] = current
                refreshed += 1
        return refreshed

    async def run_ticker(self) -> None:
        purge_at = 0.0
        while True:
            await asyncio.sleep(3)
            try:
                await self.tick()
                await self.countdown()
                await self.retry_pending()
                if self.edit_recovery is not None:
                    await self.edit_recovery.retry_pending(self.bot)
                if time.time() >= purge_at:
                    (await run_db(self.service.purge_stale))
                    for job in self._maintenance:
                        (await run_db(job))
                    purge_at = time.time() + 600
                self.last_cycle = time.monotonic()
            except Exception:
                log.exception("ticker failed")
